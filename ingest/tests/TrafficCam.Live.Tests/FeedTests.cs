using System.Net;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Npgsql;
using TrafficCam.Ingest.Database;
using TrafficCam.Ingest.Messages;
using TrafficCam.Ingest.Tests;
using Xunit;

namespace TrafficCam.Live.Tests;

/// <summary>The site running in this process, with a database of its own, filled the way ingest fills it.</summary>
sealed class Site : IAsyncDisposable
{
    readonly WebApplicationFactory<Feed> factory;
    readonly NpgsqlDataSource source;
    readonly RecordStore store;

    Site(TrafficCam.Ingest.DatabaseOptions database)
    {
        source = NpgsqlDataSource.Create(database.ConnectionString);
        store = new RecordStore(source);
        factory = new WebApplicationFactory<Feed>().WithWebHostBuilder(builder => builder
            .UseSetting("Database:Port", database.Port.ToString())
            .UseSetting("Database:Name", database.Name));
        Client = factory.CreateClient();
    }

    public HttpClient Client { get; }

    public static async Task<Site> StartAsync(Servers servers)
    {
        Assert.SkipUnless(servers.Available, "postgres or mosquitto is not installed");
        return new Site(await servers.CreateDatabaseAsync());
    }

    /// <summary>A trip seen from <paramref name="start"/> for <paramref name="seconds"/>, with the path times given as seconds into it.</summary>
    public async Task<Guid> AddPassageAsync(
        DateTimeOffset start, double seconds, string? movement = "west->east", double? junction = 3, double? exit = 6)
    {
        var id = Guid.NewGuid();
        var path = new JsonObject();
        if (junction is { } entered)
            path["junction_at"] = Stamp(start.AddSeconds(entered));
        if (exit is { } left)
            path["exit_at"] = Stamp(start.AddSeconds(left));
        var payload = Examples.Payload(
            ("id", id.ToString()),
            ("first_seen", Stamp(start)),
            ("last_seen", Stamp(start.AddSeconds(seconds))),
            ("movement", movement),
            ("stopline_crossed_at", Stamp(start.AddSeconds(2))),
            ("flags", new JsonObject { ["path"] = path }));
        Assert.True(PassageParser.TryParse(payload, out var passage, out var error), error);
        await store.StoreAsync(new Batch { Passages = [passage] }, TestContext.Current.CancellationToken);
        return id;
    }

    public async Task AddGroupStateAsync(string group, DateTimeOffset at, string state, bool settled, string? source = "observed")
    {
        var payload = Examples.GroupStatePayload(
            ("id", Guid.NewGuid().ToString()), ("ts", Stamp(at)), ("group", group), ("state", state),
            ("source", source), ("settled", settled));
        Assert.True(GroupStateParser.TryParse(payload, out var record, out var error), error);
        await store.StoreAsync(new Batch { Groups = [record] }, TestContext.Current.CancellationToken);
    }

    public async Task<JsonNode> FeedAsync(DateTimeOffset since, DateTimeOffset until)
    {
        var url = $"/api/feed?since={Uri.EscapeDataString(since.ToString("o"))}&until={Uri.EscapeDataString(until.ToString("o"))}";
        var text = await Client.GetStringAsync(url, TestContext.Current.CancellationToken);
        return JsonNode.Parse(text)!;
    }

    public async ValueTask DisposeAsync()
    {
        Client.Dispose();
        await factory.DisposeAsync();
        await source.DisposeAsync();
    }

    static string Stamp(DateTimeOffset at) => at.UtcDateTime.ToString("yyyy-MM-dd'T'HH:mm:ss.ffffff'Z'");
}

[Collection(ServersCollection.Name)]
public class FeedTests(Servers servers)
{
    static readonly DateTimeOffset Noon = new(2026, 10, 7, 12, 0, 0, TimeSpan.Zero);

    [Fact]
    public async Task Only_whole_trips_that_ended_in_the_time_asked_for_are_given()
    {
        await using var site = await Site.StartAsync(servers);
        var wanted = await site.AddPassageAsync(Noon.AddSeconds(10), seconds: 8);
        await site.AddPassageAsync(Noon.AddSeconds(-30), seconds: 8);                       // ended before
        await site.AddPassageAsync(Noon.AddSeconds(55), seconds: 8);                        // ends after
        await site.AddPassageAsync(Noon.AddSeconds(10), seconds: 8, movement: null);         // entry or exit not known
        await site.AddPassageAsync(Noon.AddSeconds(10), seconds: 8, exit: null);             // from before trips said this
        // The slip lane goes round the box, not through it: such a trip has only its stop line's time.
        var round = await site.AddPassageAsync(Noon.AddSeconds(11), seconds: 8, movement: "west->north", junction: null);

        var feed = await site.FeedAsync(Noon, Noon.AddMinutes(1));

        Assert.Equal(2, feed["passages"]!.AsArray().Count);
        var slip = feed["passages"]![1]!;
        Assert.Equal(round.ToString(), (string?)slip["id"]);
        Assert.Null(slip["junction_at"]);
        Assert.Equal(Noon.AddSeconds(13), slip["line_at"]!.GetValue<DateTimeOffset>());
        var passage = feed["passages"]![0]!;
        Assert.Equal(wanted.ToString(), (string?)passage["id"]);
        Assert.Equal("west->east", (string?)passage["movement"]);
        Assert.Equal("car", (string?)passage["class"]);
        Assert.Equal(Noon.AddSeconds(10), passage["first_seen"]!.GetValue<DateTimeOffset>());
        Assert.Equal(Noon.AddSeconds(12), passage["line_at"]!.GetValue<DateTimeOffset>());
        Assert.Equal(Noon.AddSeconds(13), passage["junction_at"]!.GetValue<DateTimeOffset>());
        Assert.Equal(Noon.AddSeconds(16), passage["exit_at"]!.GetValue<DateTimeOffset>());
        Assert.Equal(Noon.AddSeconds(18), passage["last_seen"]!.GetValue<DateTimeOffset>());
    }

    [Fact]
    public async Task A_group_comes_with_the_state_it_was_in_at_the_start_and_each_change_since()
    {
        await using var site = await Site.StartAsync(servers);
        await site.AddGroupStateAsync("west_slip", Noon.AddSeconds(-90), "green", settled: true);
        await site.AddGroupStateAsync("west_slip", Noon.AddSeconds(-40), "amber", settled: true);
        await site.AddGroupStateAsync("west_slip", Noon.AddSeconds(20), "red", settled: true, source: "inferred");
        await site.AddGroupStateAsync("west_slip", Noon.AddSeconds(70), "green", settled: true);  // after
        await site.AddGroupStateAsync("north", Noon.AddSeconds(5), "unknown", settled: true, source: null);
        // As known at once: only the latest matters.
        await site.AddGroupStateAsync("west_slip", Noon.AddSeconds(30), "unknown", settled: false, source: null);
        await site.AddGroupStateAsync("west_slip", Noon.AddSeconds(50), "red", settled: false);

        var feed = await site.FeedAsync(Noon, Noon.AddMinutes(1));

        var slip = feed["groups"]!["west_slip"]!;
        Assert.Equal(
            ["amber observed", "red inferred"],
            slip["settled"]!.AsArray().Select(change => $"{(string?)change!["state"]} {(string?)change["source"]}"));
        Assert.Equal(Noon.AddSeconds(-40), slip["settled"]![0]!["ts"]!.GetValue<DateTimeOffset>());
        Assert.Equal("red", (string?)slip["live"]!["state"]);
        var north = feed["groups"]!["north"]!;
        Assert.Equal("unknown", (string?)north["settled"]![0]!["state"]);
        Assert.Null(north["settled"]![0]!["source"]);
        Assert.Null(north["live"]);
    }

    [Fact]
    public async Task The_feed_says_what_time_it_is_and_refuses_too_long_a_stretch()
    {
        await using var site = await Site.StartAsync(servers);

        var before = DateTimeOffset.UtcNow;
        var text = await site.Client.GetStringAsync(
            $"/api/feed?since={Uri.EscapeDataString(before.AddMinutes(-2).ToString("o"))}", TestContext.Current.CancellationToken);
        var now = JsonDocument.Parse(text).RootElement.GetProperty("now").GetDateTimeOffset();
        Assert.InRange(now, before.AddSeconds(-1), DateTimeOffset.UtcNow.AddSeconds(1));

        using var tooLong = await site.Client.GetAsync(
            $"/api/feed?since={Uri.EscapeDataString(before.AddHours(-2).ToString("o"))}", TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.BadRequest, tooLong.StatusCode);
    }

    [Fact]
    public async Task The_page_and_the_drawing_are_served()
    {
        await using var site = await Site.StartAsync(servers);

        var page = await site.Client.GetStringAsync("/", TestContext.Current.CancellationToken);
        var drawing = JsonNode.Parse(await site.Client.GetStringAsync("/junction.json", TestContext.Current.CancellationToken))!;

        Assert.Contains("live.js", page);
        // Every pair of arms traffic has been seen to take has a path to be drawn along.
        string[] seen =
        [
            "west->east", "west->north", "west->south", "east->west", "east->north", "east->south",
            "north->west", "north->east", "north->south", "south->east", "south->north", "south->west",
        ];
        foreach (var movement in seen)
            Assert.NotNull(drawing["movements"]![movement]);
        // Each group that governs traffic has a stop line to colour. The pedestrian crossing is not
        // drawn: no pedestrians are, and its signal means nothing to the vehicles.
        foreach (var group in new[] { "west_ahead", "west_slip", "south", "north", "east" })
            Assert.NotNull(drawing["lamps"]![group]);
        Assert.Null(drawing["lamps"]!["crossing"]);
        // The left turn out of the south arm is forbidden, and the drawing says so.
        Assert.Contains(drawing["signs"]!.AsArray(), sign => (string?)sign!["kind"] == "no_left_turn");
    }
}
