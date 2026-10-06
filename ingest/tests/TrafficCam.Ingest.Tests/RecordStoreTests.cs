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
        Assert.Equal(4L, await Scalar(source, "SELECT count(*) FROM schemaversions"));
        Assert.Equal(
            3L,
            await Scalar(source, "SELECT count(*) FROM timescaledb_information.hypertables WHERE hypertable_name IN ('passages', 'events', 'signal_changes')"));
    }

    [Fact]
    public async Task A_passage_is_stored_with_every_column_as_sent()
    {
        Assert.SkipUnless(servers.Available, "postgres or mosquitto is not installed");
        var database = await servers.CreateDatabaseAsync();
        await using var source = NpgsqlDataSource.Create(database.ConnectionString);

        var stored = await new RecordStore(source).StoreAsync(new Batch { Passages = [Examples.Passage()] }, CancellationToken.None);

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

        Assert.Equal(1, (await store.StoreAsync(new Batch { Passages = [first] }, CancellationToken.None)).Passages);
        Assert.Equal(1, (await store.StoreAsync(new Batch { Passages = [first, second] }, CancellationToken.None)).Passages);
        Assert.Equal(0, (await store.StoreAsync(new Batch { Passages = [first, second] }, CancellationToken.None)).Passages);

        Assert.Equal(2L, await Scalar(source, "SELECT count(*) FROM passages"));
    }

    [Fact]
    public async Task An_event_is_stored_with_every_column_as_sent_and_only_once()
    {
        Assert.SkipUnless(servers.Available, "postgres or mosquitto is not installed");
        var database = await servers.CreateDatabaseAsync();
        await using var source = NpgsqlDataSource.Create(database.ConnectionString);
        var store = new RecordStore(source);

        var first = await store.StoreAsync(new Batch { Events = [Examples.Event()] }, CancellationToken.None);
        var again = await store.StoreAsync(new Batch { Events = [Examples.Event()] }, CancellationToken.None);

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
            new Batch
            {
                Passages = [Examples.Passage(Guid.NewGuid()), Examples.Passage(Guid.NewGuid())],
                Events = [Examples.Event(Guid.NewGuid())],
                Signals = [Examples.SignalChange()],
            },
            CancellationToken.None);

        Assert.Equal(new Stored(Passages: 2, Events: 1, Signals: 1), stored);
    }

    [Fact]
    public async Task A_signal_change_is_stored_with_every_column_as_sent_and_only_once()
    {
        Assert.SkipUnless(servers.Available, "postgres or mosquitto is not installed");
        var database = await servers.CreateDatabaseAsync();
        await using var source = NpgsqlDataSource.Create(database.ConnectionString);
        var store = new RecordStore(source);

        var first = await store.StoreAsync(new Batch { Signals = [Examples.SignalChange()] }, CancellationToken.None);
        var again = await store.StoreAsync(new Batch { Signals = [Examples.SignalChange()] }, CancellationToken.None);

        Assert.Equal((1, 0), (first.Signals, again.Signals));
        var row = JsonNode.Parse((string)(await Scalar(source, """
            SELECT jsonb_build_object(
                'id', id, 'camera', camera, 'config_hash', config_hash, 'head_id', head_id,
                'from_state', from_state, 'to_state', to_state, 'source', source,
                'confidence', confidence)::text
            FROM signal_changes
            """))!)!.AsObject();
        var sent = JsonNode.Parse(Examples.SignalChangeJson)!.AsObject();
        foreach (var (name, value) in row)
            Assert.True(JsonNode.DeepEquals(sent[name], value), $"{name}: sent {sent[name]}, stored {value}");
        Assert.Equal(
            sent["ts"]!.GetValue<DateTimeOffset>().UtcDateTime,
            await Scalar(source, "SELECT ts FROM signal_changes") as DateTime?);
    }

    [Fact]
    public async Task A_clip_is_stored_with_what_it_was_recorded_for_and_only_once()
    {
        Assert.SkipUnless(servers.Available, "postgres or mosquitto is not installed");
        var database = await servers.CreateDatabaseAsync();
        await using var source = NpgsqlDataSource.Create(database.ConnectionString);
        var store = new RecordStore(source);

        var first = await store.StoreAsync(new Batch { Clips = [Examples.Clip()] }, CancellationToken.None);
        var again = await store.StoreAsync(new Batch { Clips = [Examples.Clip()] }, CancellationToken.None);

        Assert.Equal((1, 0), (first.Clips, again.Clips));
        var row = JsonNode.Parse((string)(await Scalar(source, """
            SELECT jsonb_build_object(
                'id', id, 'camera', camera, 'config_hash', config_hash, 'event_id', event_id,
                'path', path, 'keyframe_path', keyframe_path, 'bytes', bytes)::text
            FROM clips
            """))!)!.AsObject();
        var sent = JsonNode.Parse(Examples.ClipJson)!.AsObject();
        foreach (var (name, value) in row)
            Assert.True(JsonNode.DeepEquals(sent[name], value), $"{name}: sent {sent[name]}, stored {value}");
        foreach (var (column, field) in new[] { ("started_at", "started_at"), ("ended_at", "ended_at"), ("closed_at", "ts") })
        {
            Assert.Equal(
                sent[field]!.GetValue<DateTimeOffset>().UtcDateTime,
                await Scalar(source, $"SELECT {column} FROM clips") as DateTime?);
        }
        // What the clip is of, and when, can be asked of the database alone.
        Assert.Equal(
            "red_light at 2026-10-04 13:05:12.345+00, manual at 2026-10-04 13:05:20+00: manual test",
            await Scalar(source, """
                SELECT string_agg(
                    concat_ws(': ', t->>'type' || ' at ' || (t->>'at')::timestamptz, t->>'reason'), ', ' ORDER BY t->>'at')
                FROM clips, jsonb_array_elements(triggers) AS t
                """, "SET TIME ZONE 'UTC'"));
        Assert.Null(await Scalar(source, "SELECT deleted_at FROM clips"));
    }

    [Fact]
    public async Task A_deleted_clip_keeps_its_row_and_the_time_it_went()
    {
        Assert.SkipUnless(servers.Available, "postgres or mosquitto is not installed");
        var database = await servers.CreateDatabaseAsync();
        await using var source = NpgsqlDataSource.Create(database.ConnectionString);
        var store = new RecordStore(source);
        var deleted = Examples.ClipDeleted();

        var unknown = await store.StoreAsync(new Batch { ClipsDeleted = [deleted] }, CancellationToken.None);
        var together = await store.StoreAsync(
            new Batch { Clips = [Examples.Clip()], ClipsDeleted = [deleted] }, CancellationToken.None);
        var again = await store.StoreAsync(new Batch { ClipsDeleted = [deleted] }, CancellationToken.None);

        Assert.Equal((0, 1, 0), (unknown.ClipsDeleted, together.ClipsDeleted, again.ClipsDeleted));
        Assert.Equal(deleted.Ts.UtcDateTime, await Scalar(source, "SELECT deleted_at FROM clips") as DateTime?);
    }

    static async Task<object?> Scalar(NpgsqlDataSource source, string sql, string? before = null)
    {
        await using var connection = await source.OpenConnectionAsync();
        if (before is not null)
        {
            await using var first = new NpgsqlCommand(before, connection);
            await first.ExecuteNonQueryAsync();
        }
        await using var command = new NpgsqlCommand(sql, connection);
        var value = await command.ExecuteScalarAsync();
        return value is DBNull ? null : value;
    }
}
