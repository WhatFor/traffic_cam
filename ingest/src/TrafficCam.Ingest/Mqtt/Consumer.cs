using System.Buffers;
using System.Diagnostics;
using System.Text;
using System.Threading.Channels;
using MQTTnet;
using MQTTnet.Formatter;
using MQTTnet.Protocol;
using Npgsql;
using Prometheus;
using TrafficCam.Contracts;
using TrafficCam.Ingest.Database;
using TrafficCam.Ingest.Messages;

namespace TrafficCam.Ingest.Mqtt;

/// <summary>
/// Stores passages and events from the broker. A message is acknowledged only once the transaction holding
/// it has committed, so whatever happens to this process the broker still has what is not stored.
/// </summary>
public sealed class Consumer(
    IngestOptions options, RecordStore store, IngestMetrics metrics, ILogger<Consumer> logger)
    : BackgroundService
{
    public const string PassagesTopic = "trafficcam/v1/passages";
    /// <summary>Followed by the event's type.</summary>
    public const string EventsTopic = "trafficcam/v1/events/";
    static readonly TimeSpan ReconnectDelay = TimeSpan.FromSeconds(2);
    const int LoggedPayloadLength = 500;

    readonly Channel<Received> received = Channel.CreateUnbounded<Received>();
    // Which connection a message arrived on. One from an earlier connection cannot be
    // acknowledged on this one; the broker sends it again instead.
    int connection;

    public bool Connected { get; private set; }

    readonly record struct Received(MqttApplicationMessageReceivedEventArgs Message, int Connection);

    protected override async Task ExecuteAsync(CancellationToken stopping)
    {
        using var client = new MqttClientFactory().CreateMqttClient();
        client.ApplicationMessageReceivedAsync += message =>
        {
            message.AutoAcknowledge = false;
            return received.Writer.WriteAsync(new Received(message, connection), stopping).AsTask();
        };
        client.DisconnectedAsync += _ =>
        {
            SetConnected(false);
            return Task.CompletedTask;
        };

        try
        {
            await Task.WhenAll(KeepConnectedAsync(client, stopping), StoreBatchesAsync(stopping));
        }
        catch (OperationCanceledException) when (stopping.IsCancellationRequested)
        {
        }
        finally
        {
            SetConnected(false);
            if (client.IsConnected)
                await client.DisconnectAsync();
        }
    }

    async Task KeepConnectedAsync(IMqttClient client, CancellationToken stopping)
    {
        var mqtt = options.Mqtt;
        var connect = new MqttClientOptionsBuilder()
            .WithTcpServer(mqtt.Host, mqtt.Port)
            .WithProtocolVersion(MqttProtocolVersion.V500)
            .WithClientId(mqtt.ClientId)
            .WithCredentials(mqtt.Username, mqtt.Password)
            // Together these make the session, and the messages queued in it, outlive the connection.
            .WithCleanStart(false)
            .WithSessionExpiryInterval(uint.MaxValue)
            .Build();
        var subscribe = new MqttClientSubscribeOptionsBuilder()
            .WithTopicFilter(PassagesTopic, MqttQualityOfServiceLevel.AtLeastOnce)
            .WithTopicFilter(EventsTopic + "+", MqttQualityOfServiceLevel.AtLeastOnce)
            .Build();

        while (!stopping.IsCancellationRequested)
        {
            if (!client.IsConnected)
            {
                try
                {
                    Interlocked.Increment(ref connection);
                    await client.ConnectAsync(connect, stopping);
                    await client.SubscribeAsync(subscribe, stopping);
                    SetConnected(true);
                    logger.LogInformation("Connected to the broker at {Host}:{Port}", mqtt.Host, mqtt.Port);
                }
                catch (Exception exception) when (exception is not OperationCanceledException)
                {
                    logger.LogWarning("Cannot connect to the broker: {Reason}", exception.Message);
                }
            }
            await Task.Delay(ReconnectDelay, stopping);
        }
    }

    async Task StoreBatchesAsync(CancellationToken stopping)
    {
        while (!stopping.IsCancellationRequested)
        {
            var batch = await ReadBatchAsync(stopping);
            var passages = new List<Passage>(batch.Count);
            var events = new List<Event>();
            foreach (var item in batch)
            {
                var topic = item.Message.ApplicationMessage.Topic;
                var payload = item.Message.ApplicationMessage.Payload.ToArray();
                var isEvent = topic.StartsWith(EventsTopic, StringComparison.Ordinal);
                string? error;
                if (isEvent)
                {
                    if (EventParser.TryParse(payload, out var @event, out error))
                    {
                        events.Add(@event);
                        continue;
                    }
                }
                else if (PassageParser.TryParse(payload, out var passage, out error))
                {
                    passages.Add(passage);
                    continue;
                }
                metrics.Invalid(isEvent ? IngestMetrics.Event : IngestMetrics.Passage).Inc();
                var text = Encoding.UTF8.GetString(payload);
                logger.LogWarning(
                    "Invalid message on {Topic}: {Error}. Payload: {Payload}",
                    topic,
                    error,
                    text.Length > LoggedPayloadLength ? text[..LoggedPayloadLength] : text);
            }

            var stored = await StoreAsync(passages, events, stopping);
            metrics.Stored(IngestMetrics.Passage).Inc(stored.Passages);
            metrics.Duplicate(IngestMetrics.Passage).Inc(passages.Count - stored.Passages);
            metrics.Stored(IngestMetrics.Event).Inc(stored.Events);
            metrics.Duplicate(IngestMetrics.Event).Inc(events.Count - stored.Events);

            foreach (var item in batch.Where(item => item.Connection == connection))
            {
                try
                {
                    await item.Message.AcknowledgeAsync(stopping);
                }
                catch (Exception exception) when (exception is not OperationCanceledException)
                {
                    // The connection went; the broker will send the message again.
                    logger.LogDebug("Could not acknowledge a message: {Reason}", exception.Message);
                }
            }
        }
    }

    /// <summary>Waits for a message, then collects what else arrives within the batch window.</summary>
    async Task<List<Received>> ReadBatchAsync(CancellationToken stopping)
    {
        var batch = new List<Received> { await received.Reader.ReadAsync(stopping) };
        using var window = CancellationTokenSource.CreateLinkedTokenSource(stopping);
        window.CancelAfter(options.BatchWindow);
        try
        {
            while (batch.Count < options.BatchSize)
                batch.Add(await received.Reader.ReadAsync(window.Token));
        }
        catch (OperationCanceledException) when (!stopping.IsCancellationRequested)
        {
        }
        return batch;
    }

    /// <summary>Keeps trying until the batch is committed; nothing is given up on.</summary>
    async Task<Stored> StoreAsync(List<Passage> passages, List<Event> events, CancellationToken stopping)
    {
        var delay = TimeSpan.FromSeconds(1);
        while (true)
        {
            var started = Stopwatch.GetTimestamp();
            try
            {
                var stored = await store.StoreAsync(passages, events, stopping);
                if (passages.Count + events.Count > 0)
                {
                    metrics.InsertSeconds.Observe(Stopwatch.GetElapsedTime(started).TotalSeconds);
                    metrics.LastStored.SetToCurrentTimeUtc();
                }
                return stored;
            }
            catch (Exception exception) when (exception is NpgsqlException or IOException or TimeoutException)
            {
                metrics.DatabaseErrors.Inc();
                logger.LogWarning(
                    "Could not store {Count} records, trying again in {Delay} s: {Reason}",
                    passages.Count + events.Count,
                    delay.TotalSeconds,
                    exception.Message);
                await Task.Delay(delay, stopping);
                delay = delay * 2 < options.MaxRetryDelay ? delay * 2 : options.MaxRetryDelay;
            }
        }
    }

    void SetConnected(bool connected)
    {
        Connected = connected;
        metrics.MqttConnected.Set(connected ? 1 : 0);
    }
}
