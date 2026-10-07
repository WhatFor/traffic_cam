using Npgsql;

namespace TrafficCam.Web;

/// <summary>
/// What a person says about a clip: that they have seen it, that it is archived, that it shows nothing
/// real, and what it shows. The only code here that writes, on connections of its own.
/// </summary>
public sealed class ClipMarks(WebOptions options) : IAsyncDisposable
{
    public const int DescriptionLength = 500;

    readonly NpgsqlDataSource dataSource = NpgsqlDataSource.Create(options.Database.ConnectionString);

    public Task ViewedAsync(Guid clip, CancellationToken cancellation) =>
        RunAsync("UPDATE clips SET viewed_at = now() WHERE id = $1 AND viewed_at IS NULL", cancellation, clip);

    public Task ArchiveAsync(Guid clip, bool archived, CancellationToken cancellation) =>
        RunAsync(
            "UPDATE clips SET archived_at = CASE WHEN $2 THEN coalesce(archived_at, now()) END WHERE id = $1",
            cancellation, clip, archived);

    public Task FalsePositiveAsync(Guid clip, bool falsePositive, CancellationToken cancellation) =>
        RunAsync(
            "UPDATE clips SET false_positive_at = CASE WHEN $2 THEN coalesce(false_positive_at, now()) END WHERE id = $1",
            cancellation, clip, falsePositive);

    /// <summary>An empty description takes it away.</summary>
    public Task DescribeAsync(Guid clip, string? description, CancellationToken cancellation)
    {
        var said = description?.Trim() ?? "";
        if (said.Length > DescriptionLength)
            said = said[..DescriptionLength];
        return RunAsync(
            "UPDATE clips SET description = nullif($2, '') WHERE id = $1", cancellation, clip, said);
    }

    public ValueTask DisposeAsync() => dataSource.DisposeAsync();

    async Task RunAsync(string sql, CancellationToken cancellation, params object[] values)
    {
        await using var command = dataSource.CreateCommand(sql);
        foreach (var value in values)
            command.Parameters.AddWithValue(value);
        await command.ExecuteNonQueryAsync(cancellation);
    }
}
