using DbUp;
using Npgsql;

namespace TrafficCam.Ingest.Database;

public static class Migrator
{
    /// <summary>
    /// How long to wait for the database. After a reboot Docker starts every container at once,
    /// whatever `depends_on` says, so ingest can get there first.
    /// </summary>
    static readonly TimeSpan WaitForDatabase = TimeSpan.FromMinutes(5);
    static readonly TimeSpan RetryDelay = TimeSpan.FromSeconds(2);

    /// <summary>Applies the SQL files from db/migrations that this database has not had yet.</summary>
    public static void Apply(string connectionString, ILoggerFactory loggers, TimeSpan? waitFor = null)
    {
        var giveUpAt = DateTime.UtcNow + (waitFor ?? WaitForDatabase);
        while (true)
        {
            var result = DeployChanges.To
                .PostgresqlDatabase(connectionString)
                .WithScriptsEmbeddedInAssembly(typeof(Migrator).Assembly, name => name.EndsWith(".sql"))
                .WithTransactionPerScript()
                .LogTo(loggers)
                .Build()
                .PerformUpgrade();
            if (result.Successful)
                return;
            if (result.Error is not NpgsqlException { IsTransient: true } || DateTime.UtcNow >= giveUpAt)
                throw new InvalidOperationException(
                    $"migration {result.ErrorScript?.Name} failed", result.Error);
            Thread.Sleep(RetryDelay);
        }
    }
}
