using Microsoft.Extensions.Logging.Abstractions;
using Npgsql;
using TrafficCam.Ingest.Database;
using Xunit;

namespace TrafficCam.Ingest.Tests;

[Collection(ServersCollection.Name)]
public class MigratorTests(Servers servers)
{
    [Fact]
    public void A_database_still_starting_up_is_worth_waiting_for() =>
        Assert.True(new PostgresException("starting up", "FATAL", "FATAL", PostgresErrorCodes.CannotConnectNow).IsTransient);

    [Fact]
    public async Task Migrations_wait_for_a_database_that_is_not_up_yet()
    {
        Assert.SkipUnless(servers.Available, "postgres or mosquitto is not installed");
        var database = await servers.CreateDatabaseAsync();
        await servers.StopDatabaseAsync();
        try
        {
            var applying = Task.Run(
                () => Migrator.Apply(database.ConnectionString, NullLoggerFactory.Instance), TestContext.Current.CancellationToken);

            await Task.Delay(TimeSpan.FromSeconds(1), TestContext.Current.CancellationToken);
            Assert.False(applying.IsCompleted);
            await servers.StartDatabaseAsync();

            await applying.WaitAsync(TimeSpan.FromSeconds(30), TestContext.Current.CancellationToken);
        }
        finally
        {
            await servers.EnsureDatabaseStartedAsync();
        }
    }

    [Fact]
    public async Task Migrations_give_up_on_a_database_that_never_comes()
    {
        Assert.SkipUnless(servers.Available, "postgres or mosquitto is not installed");
        var database = await servers.CreateDatabaseAsync();
        await servers.StopDatabaseAsync();
        try
        {
            Assert.Throws<InvalidOperationException>(() =>
                Migrator.Apply(database.ConnectionString, NullLoggerFactory.Instance, TimeSpan.FromSeconds(1)));
        }
        finally
        {
            await servers.EnsureDatabaseStartedAsync();
        }
    }

    [Fact]
    public async Task A_failure_that_waiting_will_not_mend_is_not_waited_for()
    {
        Assert.SkipUnless(servers.Available, "postgres or mosquitto is not installed");
        var database = await servers.CreateDatabaseAsync();
        var missing = new NpgsqlConnectionStringBuilder(database.ConnectionString) { Database = "no_such_database" };

        var applying = Task.Run(
            () => Migrator.Apply(missing.ConnectionString, NullLoggerFactory.Instance), TestContext.Current.CancellationToken);

        await Assert.ThrowsAsync<InvalidOperationException>(() =>
            applying.WaitAsync(TimeSpan.FromSeconds(30), TestContext.Current.CancellationToken));
    }
}
