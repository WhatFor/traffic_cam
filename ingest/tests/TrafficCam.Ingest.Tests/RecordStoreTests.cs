using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging.Abstractions;
using Npgsql;
using TrafficCam.Ingest.Database;
using Xunit;

namespace TrafficCam.Ingest.Tests;

[Collection(ServersCollection.Name)]
public class RecordStoreTests(Servers servers)
{
    [Fact]
    public async Task Migrations_apply_once_and_make_the_hypertables()
    {
        Assert.SkipUnless(servers.Available, "postgres or mosquitto is not installed");
        var database = await servers.CreateDatabaseAsync();

        Migrator.Apply(database.ConnectionString, NullLoggerFactory.Instance);

        await using var source = NpgsqlDataSource.Create(database.ConnectionString);
        Assert.Equal(2L, await Scalar(source, "SELECT count(*) FROM schemaversions"));
        Assert.Equal(
            2L,
            await Scalar(source, "SELECT count(*) FROM timescaledb_information.hypertables WHERE hypertable_name IN ('passages', 'events')"));
    }

    [Fact]
    public async Task A_passage_is_stored_with_every_column_as_sent()
    {
        Assert.SkipUnless(servers.Available, "postgres or mosquitto is not installed");
        var database = await servers.CreateDatabaseAsync();
        await using var source = NpgsqlDataSource.Create(database.ConnectionString);

        var stored = await new RecordStore(source).StoreAsync([Examples.Passage()], [], CancellationToken.None);

        Assert.Equal(new Stored(Passages: 1, Events: 0), stored);
        // Read back as JSON in the contract's shape and compared with what was sent.
        var row = JsonNode.Parse((string)(await Scalar(source, """
            SELECT jsonb_build_object(
                'id', id, 'camera', camera, 'config_hash', config_hash, 'track_id', track_id,
                'class', class, 'entry_zone', entry_zone, 'exit_zone', exit_zone, 'movement', movement,
                'stopline', stopline, 'signal_state_at_crossing', signal_state_at_crossing,
                'signal_source', signal_source, 'speed_kmh', speed_kmh, 'flags', flags)::text
            FROM passages
            """))!)!.AsObject();
        var sent = JsonNode.Parse(Examples.PassageJson)!.AsObject();
        foreach (var (name, value) in row)
            Assert.True(JsonNode.DeepEquals(sent[name], value), $"{name}: sent {sent[name]}, stored {value}");

        foreach (var column in new[] { "first_seen", "last_seen", "stopline_crossed_at" })
        {
            var sentAt = sent[column]?.GetValue<DateTimeOffset>().UtcDateTime;
            Assert.Equal(sentAt, await Scalar(source, $"SELECT {column} FROM passages") as DateTime?);
        }
    }

    [Fact]
    public async Task Storing_the_same_passages_again_adds_nothing()
    {
        Assert.SkipUnless(servers.Available, "postgres or mosquitto is not installed");
        var database = await servers.CreateDatabaseAsync();
        await using var source = NpgsqlDataSource.Create(database.ConnectionString);
        var store = new RecordStore(source);
        var first = Examples.Passage(Guid.NewGuid());
        var second = Examples.Passage(Guid.NewGuid());

        Assert.Equal(1, (await store.StoreAsync([first], [], CancellationToken.None)).Passages);
        Assert.Equal(1, (await store.StoreAsync([first, second], [], CancellationToken.None)).Passages);
        Assert.Equal(0, (await store.StoreAsync([first, second], [], CancellationToken.None)).Passages);

        Assert.Equal(2L, await Scalar(source, "SELECT count(*) FROM passages"));
    }

    [Fact]
    public async Task An_event_is_stored_with_every_column_as_sent_and_only_once()
    {
        Assert.SkipUnless(servers.Available, "postgres or mosquitto is not installed");
        var database = await servers.CreateDatabaseAsync();
        await using var source = NpgsqlDataSource.Create(database.ConnectionString);
        var store = new RecordStore(source);

        var first = await store.StoreAsync([], [Examples.Event()], CancellationToken.None);
        var again = await store.StoreAsync([], [Examples.Event()], CancellationToken.None);

        Assert.Equal(new Stored(Passages: 0, Events: 1), first);
        Assert.Equal(new Stored(Passages: 0, Events: 0), again);
        var row = JsonNode.Parse((string)(await Scalar(source, """
            SELECT jsonb_build_object(
                'id', id, 'camera', camera, 'config_hash', config_hash, 'type', type,
                'detector_version', detector_version, 'passage_id', passage_id, 'track_id', track_id,
                'class', class, 'confidence', confidence, 'attrs', attrs, 'clip_id', clip_id)::text
            FROM events
            """))!)!.AsObject();
        var sent = JsonNode.Parse(Examples.EventJson)!.AsObject();
        foreach (var (name, value) in row)
            Assert.True(JsonNode.DeepEquals(sent[name], value), $"{name}: sent {sent[name]}, stored {value}");
        Assert.Equal(
            sent["ts"]!.GetValue<DateTimeOffset>().UtcDateTime,
            await Scalar(source, "SELECT ts FROM events") as DateTime?);
    }

    [Fact]
    public async Task Passages_and_events_are_stored_together()
    {
        Assert.SkipUnless(servers.Available, "postgres or mosquitto is not installed");
        var database = await servers.CreateDatabaseAsync();
        await using var source = NpgsqlDataSource.Create(database.ConnectionString);

        var stored = await new RecordStore(source).StoreAsync(
            [Examples.Passage(Guid.NewGuid()), Examples.Passage(Guid.NewGuid())],
            [Examples.Event(Guid.NewGuid())],
            CancellationToken.None);

        Assert.Equal(new Stored(Passages: 2, Events: 1), stored);
    }

    static async Task<object?> Scalar(NpgsqlDataSource source, string sql)
    {
        await using var command = source.CreateCommand(sql);
        var value = await command.ExecuteScalarAsync();
        return value is DBNull ? null : value;
    }
}
