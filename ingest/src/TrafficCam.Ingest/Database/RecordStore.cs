using System.Text.Json;
using System.Text.Json.Serialization;
using Npgsql;
using NpgsqlTypes;
using TrafficCam.Contracts;

namespace TrafficCam.Ingest.Database;

/// <summary>How many of the records given to the store were not already there.</summary>
public readonly record struct Stored(int Passages, int Events);

public sealed class RecordStore(NpgsqlDataSource dataSource)
{
    // A record delivered twice, as QoS 1 allows, is the same row.
    const string InsertPassage = """
        INSERT INTO passages (
            id, camera, first_seen, last_seen, track_id, class, entry_zone, exit_zone, movement,
            stopline, stopline_crossed_at, signal_state_at_crossing, signal_source, speed_kmh,
            flags, config_hash)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
        ON CONFLICT (id, first_seen) DO NOTHING
        """;

    const string InsertEvent = """
        INSERT INTO events (
            id, ts, camera, type, passage_id, track_id, class, confidence, clip_id,
            config_hash, detector_version, attrs)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        ON CONFLICT (id, ts) DO NOTHING
        """;

    static readonly JsonSerializerOptions WireNames = new() { Converters = { new JsonStringEnumConverter() } };

    /// <summary>Writes everything in one transaction.</summary>
    public async Task<Stored> StoreAsync(
        IReadOnlyCollection<Passage> passages, IReadOnlyCollection<Event> events, CancellationToken cancellation)
    {
        if (passages.Count + events.Count == 0)
            return default;
        await using var connection = await dataSource.OpenConnectionAsync(cancellation);
        await using var transaction = await connection.BeginTransactionAsync(cancellation);
        await using var batch = new NpgsqlBatch(connection, transaction);
        var passageCommands = passages.Select(Command).ToList();
        var eventCommands = events.Select(Command).ToList();
        foreach (var command in passageCommands.Concat(eventCommands))
            batch.BatchCommands.Add(command);
        await batch.ExecuteNonQueryAsync(cancellation);
        await transaction.CommitAsync(cancellation);
        return new Stored(Inserted(passageCommands), Inserted(eventCommands));
    }

    static int Inserted(List<NpgsqlBatchCommand> commands) => (int)commands.Sum(command => (long)command.RecordsAffected);

    static NpgsqlBatchCommand Command(Passage passage) =>
        Command(
            InsertPassage,
            passage.Id,
            passage.Camera,
            passage.FirstSeen.UtcDateTime,
            passage.LastSeen.UtcDateTime,
            passage.TrackId,
            WireName(passage.Class),
            passage.EntryZone,
            passage.ExitZone,
            passage.Movement,
            passage.Stopline,
            passage.StoplineCrossedAt?.UtcDateTime,
            WireName(passage.SignalStateAtCrossing),
            WireName(passage.SignalSource),
            (float?)passage.SpeedKmh,
            new Jsonb(passage.Flags),
            passage.ConfigHash);

    static NpgsqlBatchCommand Command(Event @event) =>
        Command(
            InsertEvent,
            @event.Id,
            @event.Ts.UtcDateTime,
            @event.Camera,
            @event.Type,
            @event.PassageId,
            @event.TrackId,
            WireName(@event.Class),
            (float?)@event.Confidence,
            @event.ClipId,
            @event.ConfigHash,
            @event.DetectorVersion,
            new Jsonb(@event.Attrs));

    static NpgsqlBatchCommand Command(string sql, params object?[] values)
    {
        var command = new NpgsqlBatchCommand(sql);
        foreach (var value in values)
        {
            command.Parameters.Add(value is Jsonb json
                ? new NpgsqlParameter { NpgsqlDbType = NpgsqlDbType.Jsonb, Value = JsonSerializer.Serialize(json.Value) }
                : new NpgsqlParameter { Value = value ?? DBNull.Value });
        }
        return command;
    }

    /// <summary>An enum value as the contract spells it, such as red_amber.</summary>
    static string? WireName<T>(T? value) where T : struct, Enum =>
        value is { } set ? JsonSerializer.SerializeToElement(set, WireNames).GetString() : null;

    /// <summary>Marks a value to be written as a jsonb column.</summary>
    readonly record struct Jsonb(IDictionary<string, object> Value);
}
