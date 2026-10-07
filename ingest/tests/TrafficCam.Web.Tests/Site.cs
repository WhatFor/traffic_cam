using System.Buffers;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using System.Threading.Channels;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using MQTTnet;
using MQTTnet.Protocol;
using Npgsql;
using TrafficCam.Contracts;
using TrafficCam.Ingest.Database;
using TrafficCam.Ingest.Messages;
using TrafficCam.Ingest.Tests;
using Xunit;

namespace TrafficCam.Web.Tests;

/// <summary>Something a test clip was recorded for, <paramref name="Offset"/> seconds into it.</summary>
/// <param name="Attrs">The attributes of its event, as JSON. Without them there is no event row.</param>
sealed record Trigger(string Type, double Offset, string? Reason = null, string? Attrs = null);

/// <summary>The site running in this process, with a database and a clips folder of its own.</summary>
sealed class Site : IAsyncDisposable
{
    public const int ClipSeconds = 20;

    readonly WebApplicationFactory<ClipDirectory> factory;
    readonly NpgsqlDataSource source;
    readonly RecordStore store;

    Site(TrafficCam.Ingest.DatabaseOptions database, int brokerPort)
    {
        Root = Directory.CreateTempSubdirectory("trafficcam-clips-").FullName;
        source = NpgsqlDataSource.Create(database.ConnectionString);
        store = new RecordStore(source);
        factory = new WebApplicationFactory<ClipDirectory>().WithWebHostBuilder(builder => builder
            .UseSetting("Database:Port", database.Port.ToString())
            .UseSetting("Database:Name", database.Name)
            .UseSetting("Mqtt:Port", brokerPort.ToString())
            .UseSetting("Camera", Camera)
            .UseSetting("ClipsRoot", Root));
        Client = factory.CreateClient();
    }

    public const string Camera = "junction-1";

    public string Root { get; }
    public HttpClient Client { get; }

    /// <summary>What every test clip's video file holds.</summary>
    public static byte[] Video { get; } = [.. Enumerable.Range(0, 1000).Select(index => (byte)index)];

    /// <param name="brokerPort">Where the site looks for the broker, if not where the tests' broker is.</param>
    public static async Task<Site> StartAsync(Servers servers, int? brokerPort = null)
    {
        Assert.SkipUnless(servers.Available, "postgres or mosquitto is not installed");
        return new Site(await servers.CreateDatabaseAsync(), brokerPort ?? servers.BrokerPort);
    }

    /// <summary>Posts a form as a browser would, with the token a page of the site gave it.</summary>
    public async Task<HttpResponseMessage> PostAsync(string action, params (string Name, string Value)[] fields)
    {
        var cancellation = TestContext.Current.CancellationToken;
        var page = await Client.GetStringAsync("/", cancellation);
        var token = Regex.Match(page, "name=\"__RequestVerificationToken\" type=\"hidden\" value=\"([^\"]+)\"").Groups[1].Value;
        var form = fields.Append(("__RequestVerificationToken", token))
            .Select(field => KeyValuePair.Create(field.Item1, field.Item2));
        return await Client.PostAsync(action, new FormUrlEncodedContent(form), cancellation);
    }

    /// <summary>Ticks "Include viewed clips", so a clip a test has opened stays in the lists.</summary>
    public async Task IncludeViewedAsync()
    {
        using var response = await PostAsync("/?handler=Viewed", ("include", "true"));
        response.EnsureSuccessStatusCode();
    }

    /// <summary>The value of one column of a clip's row.</summary>
    public async Task<object?> ColumnAsync(Guid clip, string column)
    {
        await using var command = source.CreateCommand($"SELECT {column} FROM clips WHERE id = $1");
        command.Parameters.AddWithValue(clip);
        var value = await command.ExecuteScalarAsync(TestContext.Current.CancellationToken);
        return value is DBNull ? null : value;
    }

    /// <summary>Stores a clip the way ingest does, and writes its files unless a path is given.</summary>
    public async Task<Guid> AddClipAsync(DateTimeOffset startedAt, Trigger[] triggers, string? path = null)
    {
        var id = Guid.NewGuid();
        if (path is null)
        {
            var folder = Directory.CreateDirectory(Path.Combine(Root, startedAt.ToString("yyyy/MM/dd"))).FullName;
            path = Path.Combine(folder, id + ".mp4");
            await File.WriteAllBytesAsync(path, Video);
            await File.WriteAllBytesAsync(Path.ChangeExtension(path, ".jpg"), [0xFF, 0xD8, 0xFF]);
        }

        var batch = new Batch();
        var listed = new JsonArray();
        foreach (var trigger in triggers)
        {
            var at = Stamp(startedAt.AddSeconds(trigger.Offset));
            Guid? eventId = trigger.Attrs is null ? null : Guid.NewGuid();
            listed.Add(new JsonObject
            {
                ["type"] = trigger.Type,
                ["at"] = at,
                ["event_id"] = eventId?.ToString(),
                ["reason"] = trigger.Reason,
            });
            if (eventId is null)
                continue;
            var payload = Examples.EventPayload(
                ("id", eventId.ToString()),
                ("ts", at),
                ("type", trigger.Type),
                ("attrs", JsonNode.Parse(trigger.Attrs!)),
                ("clip_id", id.ToString()));
            Assert.True(EventParser.TryParse(payload, out var @event, out var eventError), eventError);
            batch.Events.Add(@event);
        }

        var clipPayload = Examples.ClipPayload(
            ("id", id.ToString()),
            ("event_id", listed[0]!["event_id"]?.DeepClone()),
            ("triggers", listed),
            ("path", path),
            ("keyframe_path", Path.ChangeExtension(path, ".jpg")),
            ("started_at", Stamp(startedAt)),
            ("ended_at", Stamp(startedAt.AddSeconds(ClipSeconds))),
            ("bytes", Video.Length));
        Assert.True(ClipParser.TryParse(clipPayload, out var clip, out var error), error);
        batch.Clips.Add(clip);
        await store.StoreAsync(batch, TestContext.Current.CancellationToken);
        return id;
    }

    public async Task DeleteAsync(Guid id)
    {
        var deleted = new ClipDeleted { Schema = ClipDeletedParser.Schema, Id = id, Ts = DateTimeOffset.UtcNow, Camera = "junction-1" };
        await store.StoreAsync(new Batch { ClipsDeleted = [deleted] }, TestContext.Current.CancellationToken);
    }

    public async ValueTask DisposeAsync()
    {
        Client.Dispose();
        await factory.DisposeAsync();
        await source.DisposeAsync();
        Directory.Delete(Root, recursive: true);
    }

    static string Stamp(DateTimeOffset at) => at.UtcDateTime.ToString("yyyy-MM-dd'T'HH:mm:ss.ffffff'Z'");
}

/// <summary>Hears what is published to the broker on one topic, including what is retained there.</summary>
sealed class Listener : IAsyncDisposable
{
    static readonly TimeSpan Patience = TimeSpan.FromSeconds(5);
    readonly IMqttClient client = new MqttClientFactory().CreateMqttClient();
    readonly Channel<JsonNode> heard = Channel.CreateUnbounded<JsonNode>();

    public static async Task<Listener> StartAsync(Servers servers, string topic)
    {
        var listener = new Listener();
        listener.client.ApplicationMessageReceivedAsync += message =>
        {
            var payload = message.ApplicationMessage.Payload.ToArray();
            return payload.Length == 0
                ? Task.CompletedTask
                : listener.heard.Writer.WriteAsync(JsonNode.Parse(payload)!).AsTask();
        };
        var cancellation = TestContext.Current.CancellationToken;
        await listener.client.ConnectAsync(
            new MqttClientOptionsBuilder().WithTcpServer("127.0.0.1", servers.BrokerPort).Build(), cancellation);
        await listener.client.SubscribeAsync(topic, MqttQualityOfServiceLevel.AtLeastOnce, cancellation);
        return listener;
    }

    public async Task<JsonNode> NextAsync()
    {
        using var patience = CancellationTokenSource.CreateLinkedTokenSource(TestContext.Current.CancellationToken);
        patience.CancelAfter(Patience);
        return await heard.Reader.ReadAsync(patience.Token);
    }

    public async ValueTask DisposeAsync()
    {
        await client.DisconnectAsync();
        client.Dispose();
    }
}
