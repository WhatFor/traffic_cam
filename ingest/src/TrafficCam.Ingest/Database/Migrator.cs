using DbUp;

namespace TrafficCam.Ingest.Database;

public static class Migrator
{
    /// <summary>Applies the SQL files from db/migrations that this database has not had yet.</summary>
    public static void Apply(string connectionString, ILoggerFactory loggers)
    {
        var result = DeployChanges.To
            .PostgresqlDatabase(connectionString)
            .WithScriptsEmbeddedInAssembly(typeof(Migrator).Assembly, name => name.EndsWith(".sql"))
            .WithTransactionPerScript()
            .LogTo(loggers)
            .Build()
            .PerformUpgrade();
        if (!result.Successful)
            throw new InvalidOperationException(
                $"migration {result.ErrorScript?.Name} failed", result.Error);
    }
}
