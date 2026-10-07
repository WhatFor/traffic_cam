using System.Text.Json;
using System.Text.Json.Serialization;
using Npgsql;
using NpgsqlTypes;
using TrafficCam.Contracts;

namespace TrafficCam.Ingest.Database;

/// <summary>Records of each kind, to be written together.</summary>
public sealed class Batch
{
    public List<Passage> Passages { get; init; } = [];
    public List<Event> Events { get; init; } = [];
    public List<SignalChange> Signals { get; init; } = [];
    public List<GroupState> Groups { get; init; } = [];
    public List<Clip> Clips { get; init; } = [];
    public List<ClipDeleted> ClipsDeleted { get; init; } = [];

    public int Count =>
        Passages.Count + Events.Count + Signals.Count + Groups.Count + Clips.Count + ClipsDeleted.Count;
}

/// <summary>How many of the records given to the store changed something: were not already there.</summary>
public readonly record struct Stored(
    int Passages, int Events, int Signals = 0, int Clips = 0, int ClipsDeleted = 0, int Groups = 0);

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

    // The broker keeps each group's last live state too, and sends it again likewise.
    const string InsertGroupState = """
        INSERT INTO group_states (id, ts, camera, group_id, state, source, settled, config_hash)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (id, ts) DO NOTHING
        """;

    // The broker keeps each head's last change and sends it again on every subscription.
    const string InsertSignalChange = """
        INSERT INTO signal_changes (
            id, ts, camera, head_id, from_state, to_state, source, confidence, config_hash)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT (id, ts) DO NOTHING
        """;

    const string InsertClip = """
        INSERT INTO clips (
            id, camera, event_id, triggers, path, keyframe_path, started_at, ended_at, closed_at,
            bytes, config_hash)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        ON CONFLICT (id) DO NOTHING
        """;

    const string MarkClipDeleted = """
        UPDATE clips SET deleted_at = $2 WHERE id = $1 AND deleted_at IS NULL
        """;

    static readonly JsonSerializerOptions WireNames = new() { Converters = { new JsonStringEnumConverter() } };

    /// <summary>Writes everything in one transaction.</summary>
    public async Task<Stored> StoreAsync(Batch records, CancellationToken cancellation)
    {
        if (records.Count == 0)
            return default;
        await using var connection = await dataSource.OpenConnectionAsync(cancellation);
        await using var transaction = await connection.BeginTransactionAsync(cancellation);
        await using var batch = new NpgsqlBatch(connection, transaction);
        var passages = records.Passages.Select(Command).ToList();
        var events = records.Events.Select(Command).ToList();
        var signals = records.Signals.Select(Command).ToList();
        var groups = records.Groups.Select(Command).ToList();
        var clips = records.Clips.Select(Command).ToList();
        // After the clips, so one announced and deleted in the same batch ends up deleted.
        var clipsDeleted = records.ClipsDeleted.Select(Command).ToList();
        var commands = passages.Concat(events).Concat(signals).Concat(groups).Concat(clips).Concat(clipsDeleted);
        foreach (var command in commands)
            batch.BatchCommands.Add(command);
        await batch.ExecuteNonQueryAsync(cancellation);
        await transaction.CommitAsync(cancellation);
        return new Stored(
            Changed(passages), Changed(events), Changed(signals), Changed(clips), Changed(clipsDeleted),
            Changed(groups));
    }

    static int Changed(List<NpgsqlBatchCommand> commands) => (int)commands.Sum(command => (long)command.RecordsAffected);

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

    static NpgsqlBatchCommand Command(SignalChange change) =>
        Command(
            InsertSignalChange,
            change.Id,
            change.Ts.UtcDateTime,
            change.Camera,
            change.HeadId,
            WireName(change.FromState),
            WireName((SignalState?)change.ToState),
            WireName((SignalSource?)change.Source),
            (float?)change.Confidence,
            change.ConfigHash);

    static NpgsqlBatchCommand Command(GroupState state) =>
        Command(
            InsertGroupState,
            state.Id,
            state.Ts.UtcDateTime,
            state.Camera,
            state.Group,
            WireName((SignalState?)state.State),
            WireName(state.Source),
            state.Settled,
            state.ConfigHash);

    static NpgsqlBatchCommand Command(Clip clip) =>
        Command(
            InsertClip,
            clip.Id,
            clip.Camera,
            clip.EventId,
            new Jsonb(clip.Triggers),
            clip.Path,
            clip.KeyframePath,
            clip.StartedAt.UtcDateTime,
            clip.EndedAt.UtcDateTime,
            clip.Ts.UtcDateTime,
            clip.Bytes,
            clip.ConfigHash);

    static NpgsqlBatchCommand Command(ClipDeleted deleted) =>
        Command(MarkClipDeleted, deleted.Id, deleted.Ts.UtcDateTime);

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

    /// <summary>Marks a value to be written as a jsonb column, in the contract's own JSON form.</summary>
    readonly record struct Jsonb(object Value);
}
