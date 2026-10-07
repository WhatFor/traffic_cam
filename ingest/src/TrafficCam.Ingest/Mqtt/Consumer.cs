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
/// Stores the records vision publishes: passages, events, signal changes and clips. A message is acknowledged only once
/// the transaction holding it has committed, so whatever happens to this process the broker still has what is not stored.
/// </summary>
public sealed class Consumer(
    IngestOptions options, RecordStore store, IngestMetrics metrics, ILogger<Consumer> logger)
    : BackgroundService
{
    public const string PassagesTopic = "trafficcam/v1/passages";
    /// <summary>Followed by the event's type.</summary>
    public const string EventsTopic = "trafficcam/v1/events/";
    /// <summary>Followed by the signal head's name.</summary>
    public const string SignalsTopic = "trafficcam/v1/signals/";
    /// <summary>Followed by the group's name, and by <see cref="SettledSuffix"/> for its settled state.</summary>
    public const string GroupsTopic = "trafficcam/v1/groups/";
    public const string SettledSuffix = "/settled";
    /// <summary>Followed by the clip's id, and by <see cref="DeletedSuffix"/> once its files are gone.</summary>
    public const string ClipsTopic = "trafficcam/v1/clips/";
    public const string DeletedSuffix = "/deleted";
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
            .WithTopicFilter(SignalsTopic + "+", MqttQualityOfServiceLevel.AtLeastOnce)
            .WithTopicFilter(GroupsTopic + "+", MqttQualityOfServiceLevel.AtLeastOnce)
            .WithTopicFilter(GroupsTopic + "+" + SettledSuffix, MqttQualityOfServiceLevel.AtLeastOnce)
            .WithTopicFilter(ClipsTopic + "+", MqttQualityOfServiceLevel.AtLeastOnce)
            .WithTopicFilter(ClipsTopic + "+" + DeletedSuffix, MqttQualityOfServiceLevel.AtLeastOnce)
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
            var records = new Batch();
            foreach (var item in batch)
            {
                var topic = item.Message.ApplicationMessage.Topic;
                var payload = item.Message.ApplicationMessage.Payload.ToArray();
                var (kind, error) = Add(records, topic, payload);
                if (error is null)
                    continue;
                metrics.Invalid(kind).Inc();
                var text = Encoding.UTF8.GetString(payload);
                logger.LogWarning(
                    "Invalid message on {Topic}: {Error}. Payload: {Payload}",
                    topic,
                    error,
                    text.Length > LoggedPayloadLength ? text[..LoggedPayloadLength] : text);
            }

            var stored = await StoreAsync(records, stopping);
            Count(IngestMetrics.Passage, records.Passages.Count, stored.Passages);
            Count(IngestMetrics.Event, records.Events.Count, stored.Events);
            Count(IngestMetrics.Signal, records.Signals.Count, stored.Signals);
            Count(IngestMetrics.Group, records.Groups.Count, stored.Groups);
            Count(IngestMetrics.Clip, records.Clips.Count, stored.Clips);
            Count(IngestMetrics.ClipDeleted, records.ClipsDeleted.Count, stored.ClipsDeleted);

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

    /// <summary>Parses a message as the kind of record its topic carries and adds it to the batch.</summary>
    static (string Kind, string? Error) Add(Batch records, string topic, byte[] payload)
    {
        string? error;
        if (topic.StartsWith(EventsTopic, StringComparison.Ordinal))
        {
            if (EventParser.TryParse(payload, out var @event, out error))
                records.Events.Add(@event);
            return (IngestMetrics.Event, error);
        }
        if (topic.StartsWith(SignalsTopic, StringComparison.Ordinal))
        {
            if (SignalChangeParser.TryParse(payload, out var change, out error))
                records.Signals.Add(change);
            return (IngestMetrics.Signal, error);
        }
        if (topic.StartsWith(GroupsTopic, StringComparison.Ordinal))
        {
            if (GroupStateParser.TryParse(payload, out var state, out error))
                records.Groups.Add(state);
            return (IngestMetrics.Group, error);
        }
        if (topic.StartsWith(ClipsTopic, StringComparison.Ordinal))
        {
            if (topic.EndsWith(DeletedSuffix, StringComparison.Ordinal))
            {
                if (ClipDeletedParser.TryParse(payload, out var deleted, out error))
                    records.ClipsDeleted.Add(deleted);
                return (IngestMetrics.ClipDeleted, error);
            }
            if (ClipParser.TryParse(payload, out var clip, out error))
                records.Clips.Add(clip);
            return (IngestMetrics.Clip, error);
        }
        if (PassageParser.TryParse(payload, out var passage, out error))
            records.Passages.Add(passage);
        return (IngestMetrics.Passage, error);
    }

    void Count(string kind, int received, int stored)
    {
        metrics.Stored(kind).Inc(stored);
        metrics.Duplicate(kind).Inc(received - stored);
    }

    /// <summary>Keeps trying until the batch is committed; nothing is given up on.</summary>
    async Task<Stored> StoreAsync(Batch records, CancellationToken stopping)
    {
        var delay = TimeSpan.FromSeconds(1);
        while (true)
        {
            var started = Stopwatch.GetTimestamp();
            try
            {
                var stored = await store.StoreAsync(records, stopping);
                if (records.Count > 0)
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
                    records.Count,
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
