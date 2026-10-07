using Npgsql;

namespace TrafficCam.Live;

/// <summary>A group's state from a moment on.</summary>
public sealed record GroupChange(DateTimeOffset Ts, string State, string? Source);

/// <summary>
/// One group of signal heads: its settled states over the time asked for, beginning with the one in
/// force at its start, and its state as last known at once.
/// </summary>
public sealed record GroupFeed(IReadOnlyList<GroupChange> Settled, GroupChange? Live);

/// <summary>
/// A vehicle's trip through the junction, with the times that pace a drawing of it. One that goes round
/// the box and not through it, as the slip lane does, has no time for the junction, only for its stop line.
/// </summary>
public sealed record PassageFeed(
    Guid Id, string? Class, string Movement, DateTimeOffset FirstSeen, DateTimeOffset? LineAt,
    DateTimeOffset? JunctionAt, DateTimeOffset ExitAt, DateTimeOffset LastSeen);

/// <summary>Everything the page draws from. This payload is the whole interface to the page.</summary>
public sealed record FeedPayload(
    DateTimeOffset Now, IReadOnlyDictionary<string, GroupFeed> Groups, IReadOnlyList<PassageFeed> Passages);

/// <summary>Reads what the page needs between two moments.</summary>
public sealed class Feed(NpgsqlDataSource dataSource)
{
    // No trip lasts this long: it bounds how far back the passages table is searched.
    static readonly TimeSpan LongestTrip = TimeSpan.FromMinutes(10);

    public async Task<FeedPayload> ReadAsync(DateTimeOffset since, DateTimeOffset until, CancellationToken cancellation)
    {
        var groups = new SortedDictionary<string, (List<GroupChange> Settled, GroupChange? Live)>(StringComparer.Ordinal);
        (List<GroupChange> Settled, GroupChange? Live) Of(string group) =>
            groups.TryGetValue(group, out var found) ? found : groups[group] = ([], null);

        // Each group's settled state at the start, then every change of it after.
        await using (var command = dataSource.CreateCommand("""
            (SELECT DISTINCT ON (group_id) group_id, ts, state, source
             FROM group_states WHERE settled AND ts <= $1 ORDER BY group_id, ts DESC)
            UNION ALL
            (SELECT group_id, ts, state, source FROM group_states WHERE settled AND ts > $1 AND ts <= $2)
            ORDER BY 1, 2
            """))
        {
            command.Parameters.AddWithValue(since.UtcDateTime);
            command.Parameters.AddWithValue(until.UtcDateTime);
            await using var reader = await command.ExecuteReaderAsync(cancellation);
            while (await reader.ReadAsync(cancellation))
                Of(reader.GetString(0)).Settled.Add(Change(reader));
        }

        await using (var command = dataSource.CreateCommand("""
            SELECT DISTINCT ON (group_id) group_id, ts, state, source
            FROM group_states WHERE NOT settled AND ts <= $1 ORDER BY group_id, ts DESC
            """))
        {
            command.Parameters.AddWithValue(until.UtcDateTime);
            await using var reader = await command.ExecuteReaderAsync(cancellation);
            while (await reader.ReadAsync(cancellation))
                groups[reader.GetString(0)] = (Of(reader.GetString(0)).Settled, Change(reader));
        }

        // Only trips whose entry and exit are both known, and that say when they reached the exit.
        var passages = new List<PassageFeed>();
        await using (var command = dataSource.CreateCommand("""
            SELECT id, class, movement, first_seen, stopline_crossed_at,
                   (flags->'path'->>'junction_at')::timestamptz, (flags->'path'->>'exit_at')::timestamptz, last_seen
            FROM passages
            WHERE first_seen > $1 - $3 AND last_seen > $1 AND last_seen <= $2
              AND movement IS NOT NULL
              AND flags->'path' ? 'exit_at'
            ORDER BY first_seen
            """))
        {
            command.Parameters.AddWithValue(since.UtcDateTime);
            command.Parameters.AddWithValue(until.UtcDateTime);
            command.Parameters.AddWithValue(LongestTrip);
            await using var reader = await command.ExecuteReaderAsync(cancellation);
            while (await reader.ReadAsync(cancellation))
                passages.Add(new PassageFeed(
                    reader.GetGuid(0),
                    reader.IsDBNull(1) ? null : reader.GetString(1),
                    reader.GetString(2),
                    reader.GetFieldValue<DateTime>(3),
                    reader.IsDBNull(4) ? null : reader.GetFieldValue<DateTime>(4),
                    reader.IsDBNull(5) ? null : reader.GetFieldValue<DateTime>(5),
                    reader.GetFieldValue<DateTime>(6),
                    reader.GetFieldValue<DateTime>(7)));
        }

        return new FeedPayload(
            DateTimeOffset.UtcNow,
            groups.ToDictionary(group => group.Key, group => new GroupFeed(group.Value.Settled, group.Value.Live)),
            passages);
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

    static GroupChange Change(NpgsqlDataReader reader) =>
        new(reader.GetFieldValue<DateTime>(1), reader.GetString(2), reader.IsDBNull(3) ? null : reader.GetString(3));
}
