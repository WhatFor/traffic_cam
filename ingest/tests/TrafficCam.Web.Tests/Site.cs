using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
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

    Site(TrafficCam.Ingest.DatabaseOptions database)
    {
        Root = Directory.CreateTempSubdirectory("trafficcam-clips-").FullName;
        source = NpgsqlDataSource.Create(database.ConnectionString);
        store = new RecordStore(source);
        factory = new WebApplicationFactory<ClipDirectory>().WithWebHostBuilder(builder => builder
            .UseSetting("Database:Port", database.Port.ToString())
            .UseSetting("Database:Name", database.Name)
            .UseSetting("ClipsRoot", Root));
        Client = factory.CreateClient();
    }

    public string Root { get; }
    public HttpClient Client { get; }

    /// <summary>What every test clip's video file holds.</summary>
    public static byte[] Video { get; } = [.. Enumerable.Range(0, 1000).Select(index => (byte)index)];

    public static async Task<Site> StartAsync(Servers servers)
    {
        Assert.SkipUnless(servers.Available, "postgres or mosquitto is not installed");
        return new Site(await servers.CreateDatabaseAsync());
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
