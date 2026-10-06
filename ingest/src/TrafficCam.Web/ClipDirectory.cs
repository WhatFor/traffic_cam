using Npgsql;

namespace TrafficCam.Web;

/// <summary>One thing a clip was recorded for: an entry in a type's list.</summary>
public sealed record ClipEntry(
    Guid ClipId, string Type, DateTimeOffset At, double OffsetSeconds, double LengthSeconds, string? Detail, bool HasStill);

/// <summary>A type's newest entries, and how many it has in all.</summary>
public sealed record TypeColumn(string Type, long Total, IReadOnlyList<ClipEntry> Newest);

public sealed record StoredClip(
    Guid Id, DateTimeOffset StartedAt, double LengthSeconds, long Bytes, string Path, string? StillPath, DateTimeOffset? DeletedAt);

/// <summary>Reads the clips table. A clip appears once under each type it was recorded for.</summary>
public sealed class ClipDirectory(NpgsqlDataSource dataSource)
{
    // A trigger that came from an event carries the event's id and time, which find its row.
    const string Entries = """
        SELECT c.id, t->>'type' AS type, (t->>'at')::timestamptz AS at,
               extract(epoch FROM (t->>'at')::timestamptz - c.started_at)::float8 AS offset_s,
               extract(epoch FROM c.ended_at - c.started_at)::float8 AS length_s,
               t->>'reason' AS reason, e.attrs::text AS attrs,
               c.keyframe_path IS NOT NULL AS has_still, c.deleted_at
        FROM clips c
        CROSS JOIN LATERAL jsonb_array_elements(c.triggers) AS t
        LEFT JOIN events e ON e.id = (t->>'event_id')::uuid AND e.ts = (t->>'at')::timestamptz
        """;

    /// <summary>Every type that has clips, each with its newest entries.</summary>
    public async Task<IReadOnlyList<TypeColumn>> ColumnsAsync(int newest, CancellationToken cancellation)
    {
        await using var command = dataSource.CreateCommand($"""
            SELECT * FROM (
                SELECT *, row_number() OVER (PARTITION BY type ORDER BY at DESC, id) AS position,
                       count(*) OVER (PARTITION BY type) AS total
                FROM ({Entries} WHERE c.deleted_at IS NULL) entries
            ) ranked
            WHERE position <= $1
            ORDER BY type, position
            """);
        command.Parameters.AddWithValue(newest);
        await using var reader = await command.ExecuteReaderAsync(cancellation);
        var columns = new List<(string Type, long Total, List<ClipEntry> Entries)>();
        while (await reader.ReadAsync(cancellation))
        {
            var entry = Entry(reader);
            if (columns.Count == 0 || columns[^1].Type != entry.Type)
                columns.Add((entry.Type, reader.GetInt64(reader.GetOrdinal("total")), []));
            columns[^1].Entries.Add(entry);
        }
        return [.. columns.Select(column => new TypeColumn(column.Type, column.Total, column.Entries))];
    }

    /// <summary>One type's entries, newest first, from just before <paramref name="before"/> if given.</summary>
    public async Task<IReadOnlyList<ClipEntry>> OfTypeAsync(
        string type, DateTimeOffset? before, int limit, CancellationToken cancellation)
    {
        await using var command = dataSource.CreateCommand($"""
            SELECT * FROM ({Entries} WHERE c.deleted_at IS NULL) entries
            WHERE type = $1 AND ($2::timestamptz IS NULL OR at < $2)
            ORDER BY at DESC, id
            LIMIT $3
            """);
        command.Parameters.AddWithValue(type);
        command.Parameters.Add(new NpgsqlParameter<DateTime?> { TypedValue = before?.UtcDateTime });
        command.Parameters.AddWithValue(limit);
        return await EntriesAsync(command, cancellation);
    }

    /// <summary>Everything one clip was recorded for, in the order it happened. Works for a deleted clip too.</summary>
    public async Task<IReadOnlyList<ClipEntry>> TriggersAsync(Guid clip, CancellationToken cancellation)
    {
        await using var command = dataSource.CreateCommand($"{Entries} WHERE c.id = $1 ORDER BY at");
        command.Parameters.AddWithValue(clip);
        return await EntriesAsync(command, cancellation);
    }

    public async Task<StoredClip?> FindAsync(Guid id, CancellationToken cancellation)
    {
        await using var command = dataSource.CreateCommand("""
            SELECT started_at, extract(epoch FROM ended_at - started_at)::float8, bytes, path, keyframe_path, deleted_at
            FROM clips WHERE id = $1
            """);
        command.Parameters.AddWithValue(id);
        await using var reader = await command.ExecuteReaderAsync(cancellation);
        if (!await reader.ReadAsync(cancellation))
            return null;
        return new StoredClip(
            id,
            reader.GetFieldValue<DateTime>(0),
            reader.GetDouble(1),
            reader.GetInt64(2),
            reader.GetString(3),
            reader.IsDBNull(4) ? null : reader.GetString(4),
            reader.IsDBNull(5) ? null : reader.GetFieldValue<DateTime>(5));
    }

    public async Task<bool> ReachableAsync(CancellationToken cancellation)
    {
        try
        {
            await using var command = dataSource.CreateCommand("SELECT 1");
            await command.ExecuteScalarAsync(cancellation);
            return true;
        }
        catch (Exception exception) when (exception is NpgsqlException or IOException or TimeoutException)
        {
            return false;
        }
    }

    static async Task<IReadOnlyList<ClipEntry>> EntriesAsync(NpgsqlCommand command, CancellationToken cancellation)
    {
        await using var reader = await command.ExecuteReaderAsync(cancellation);
        var entries = new List<ClipEntry>();
        while (await reader.ReadAsync(cancellation))
            entries.Add(Entry(reader));
        return entries;
    }

    static ClipEntry Entry(NpgsqlDataReader reader)
    {
        string? Text(string column) =>
            reader.IsDBNull(reader.GetOrdinal(column)) ? null : reader.GetString(reader.GetOrdinal(column));

        var type = reader.GetString(reader.GetOrdinal("type"));
        return new ClipEntry(
            reader.GetGuid(reader.GetOrdinal("id")),
            type,
            reader.GetFieldValue<DateTime>(reader.GetOrdinal("at")),
            reader.GetDouble(reader.GetOrdinal("offset_s")),
            reader.GetDouble(reader.GetOrdinal("length_s")),
            Wording.Detail(type, Text("reason"), Text("attrs")),
            reader.GetBoolean(reader.GetOrdinal("has_still")));
    }
}
