using System.Text.Json;
using System.Text.Json.Serialization;
using Npgsql;
using NpgsqlTypes;
using TrafficCam.Contracts;

namespace TrafficCam.Ingest.Database;

public sealed class PassageStore(NpgsqlDataSource dataSource)
{
    // A passage delivered twice, as QoS 1 allows, is the same row.
    const string Insert = """
        INSERT INTO passages (
            id, camera, first_seen, last_seen, track_id, class, entry_zone, exit_zone, movement,
            stopline, stopline_crossed_at, signal_state_at_crossing, signal_source, speed_kmh,
            flags, config_hash)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
        ON CONFLICT (id, first_seen) DO NOTHING
        """;

    static readonly JsonSerializerOptions WireNames = new() { Converters = { new JsonStringEnumConverter() } };

    /// <summary>Writes the passages in one transaction. Returns how many were not already there.</summary>
    public async Task<int> StoreAsync(IReadOnlyCollection<Passage> passages, CancellationToken cancellation)
    {
        if (passages.Count == 0)
            return 0;
        await using var connection = await dataSource.OpenConnectionAsync(cancellation);
        await using var transaction = await connection.BeginTransactionAsync(cancellation);
        await using var batch = new NpgsqlBatch(connection, transaction);
        foreach (var passage in passages)
            batch.BatchCommands.Add(Command(passage));
        var stored = await batch.ExecuteNonQueryAsync(cancellation);
        await transaction.CommitAsync(cancellation);
        return stored;
    }

    static NpgsqlBatchCommand Command(Passage passage)
    {
        var command = new NpgsqlBatchCommand(Insert);
        object?[] values =
        [
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
        ];
        foreach (var value in values)
            command.Parameters.Add(new NpgsqlParameter { Value = value ?? DBNull.Value });
        command.Parameters.Add(
            new NpgsqlParameter { NpgsqlDbType = NpgsqlDbType.Jsonb, Value = JsonSerializer.Serialize(passage.Flags) });
        command.Parameters.Add(new NpgsqlParameter { Value = passage.ConfigHash });
        return command;
    }

    /// <summary>An enum value as the contract spells it, such as red_amber.</summary>
    static string? WireName<T>(T? value) where T : struct, Enum =>
        value is { } set ? JsonSerializer.SerializeToElement(set, WireNames).GetString() : null;
}
