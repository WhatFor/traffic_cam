using Microsoft.Extensions.Logging.Abstractions;
using MQTTnet;
using MQTTnet.Protocol;
using Npgsql;
using Prometheus;
using TrafficCam.Ingest.Database;
using TrafficCam.Ingest.Mqtt;
using Xunit;

namespace TrafficCam.Ingest.Tests;

/// <summary>The consumer against a real broker and a real database.</summary>
[Collection(ServersCollection.Name)]
public class ConsumerTests(Servers servers)
{
    static readonly TimeSpan Patience = TimeSpan.FromSeconds(20);

    [Fact]
    public async Task A_passage_published_twice_is_stored_once()
    {
        await using var ingest = await Ingest.StartAsync(servers);
        var payload = Examples.PayloadWithId(Guid.NewGuid());

        await ingest.PublishAsync(payload);
        await ingest.PublishAsync(payload);

        await Eventually(() => ingest.Metrics.Stored.Value + ingest.Metrics.Duplicate.Value == 2);
        Assert.Equal(1, await ingest.RowsAsync());
        Assert.Equal(1, ingest.Metrics.Stored.Value);
        Assert.Equal(1, ingest.Metrics.Duplicate.Value);
    }

    [Fact]
    public async Task Passages_published_while_ingest_is_stopped_are_stored_when_it_returns()
    {
        var first = await Ingest.StartAsync(servers);
        await first.StopAsync();

        for (var count = 0; count < 3; count++)
            await first.PublishAsync(Examples.PayloadWithId(Guid.NewGuid()));
        Assert.Equal(0, await first.RowsAsync());

        await using var second = await Ingest.StartAsync(servers, first.Options);
        await Eventually(async () => await second.RowsAsync() == 3);
        await first.DisposeAsync();
    }

    [Fact]
    public async Task An_invalid_message_is_counted_and_does_not_hold_up_the_next()
    {
        await using var ingest = await Ingest.StartAsync(servers);

        await ingest.PublishAsync("not a passage"u8.ToArray());
        await ingest.PublishAsync(Examples.PayloadWithId(Guid.NewGuid()));

        await Eventually(async () => await ingest.RowsAsync() == 1);
        Assert.Equal(1, ingest.Metrics.Invalid.Value);
    }

    [Fact]
    public async Task With_the_database_down_nothing_is_lost()
    {
        await using var ingest = await Ingest.StartAsync(servers);
        await servers.StopDatabaseAsync();
        try
        {
            await ingest.PublishAsync(Examples.PayloadWithId(Guid.NewGuid()));
            await Eventually(() => ingest.Metrics.DatabaseErrors.Value >= 1);
        }
        finally
        {
            await servers.StartDatabaseAsync();
        }

        await Eventually(async () => await ingest.RowsAsync() == 1);
    }

    static Task Eventually(Func<bool> condition) => Eventually(() => Task.FromResult(condition()));

    static async Task Eventually(Func<Task<bool>> condition)
    {
        var deadline = DateTime.UtcNow + Patience;
        while (!await condition())
        {
            Assert.True(DateTime.UtcNow < deadline, "timed out");
            await Task.Delay(50, TestContext.Current.CancellationToken);
        }
    }

    /// <summary>A running consumer with its own database and its own session on the broker.</summary>
    sealed class Ingest : IAsyncDisposable
    {
        readonly NpgsqlDataSource source;
        readonly Consumer consumer;
        bool stopped;

        Ingest(IngestOptions options)
        {
            Options = options;
            Metrics = new IngestMetrics(Prometheus.Metrics.NewCustomRegistry());
            source = NpgsqlDataSource.Create(options.Database.ConnectionString);
            consumer = new Consumer(options, new PassageStore(source), Metrics, NullLogger<Consumer>.Instance);
        }

        public IngestOptions Options { get; }
        public IngestMetrics Metrics { get; }

        public static async Task<Ingest> StartAsync(Servers servers, IngestOptions? options = null)
        {
            Assert.SkipUnless(servers.Available, "postgres or mosquitto is not installed");
            options ??= new IngestOptions
            {
                Mqtt = new MqttOptions { Port = servers.BrokerPort, ClientId = "ingest-" + Guid.NewGuid() },
                Database = await servers.CreateDatabaseAsync(),
                BatchWindow = TimeSpan.FromMilliseconds(50),
                MaxRetryDelay = TimeSpan.FromMilliseconds(200),
            };
            var ingest = new Ingest(options);
            await ingest.consumer.StartAsync(TestContext.Current.CancellationToken);
            await Eventually(() => ingest.consumer.Connected);
            return ingest;
        }

        public async Task PublishAsync(byte[] payload)
        {
            using var client = new MqttClientFactory().CreateMqttClient();
            await client.ConnectAsync(
                new MqttClientOptionsBuilder().WithTcpServer("127.0.0.1", Options.Mqtt.Port).Build(),
                TestContext.Current.CancellationToken);
            await client.PublishAsync(
                new MqttApplicationMessageBuilder()
                    .WithTopic(Consumer.PassagesTopic)
                    .WithPayload(payload)
                    .WithQualityOfServiceLevel(MqttQualityOfServiceLevel.AtLeastOnce)
                    .Build(),
                TestContext.Current.CancellationToken);
            await client.DisconnectAsync();
        }

        /// <summary>Rows in the table; -1 while the database cannot be asked.</summary>
        public async Task<long> RowsAsync()
        {
            try
            {
                await using var command = source.CreateCommand("SELECT count(*) FROM passages");
                return (long)(await command.ExecuteScalarAsync())!;
            }
            catch (NpgsqlException)
            {
                return -1;
            }
        }

        public async Task StopAsync()
        {
            if (stopped)
                return;
            stopped = true;
            await consumer.StopAsync(CancellationToken.None);
        }

        public async ValueTask DisposeAsync()
        {
            await StopAsync();
            consumer.Dispose();
            await source.DisposeAsync();
        }
    }
}
