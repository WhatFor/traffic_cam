using System.Text.Json;
using Npgsql;
using TrafficCam.Live;

// The runtime image has no HTTP client, so the container's health check runs this program.
if (args.Contains("--healthcheck"))
{
    var port = Environment.GetEnvironmentVariable("ASPNETCORE_HTTP_PORTS") ?? "8080";
    using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(5) };
    try
    {
        using var response = await http.GetAsync($"http://127.0.0.1:{port}/healthz");
        return response.IsSuccessStatusCode ? 0 : 1;
    }
    catch (HttpRequestException)
    {
        return 1;
    }
}

var builder = WebApplication.CreateBuilder(args);
builder.Logging.AddSimpleConsole(console => console.SingleLine = true);
// Otherwise every request is logged.
builder.Logging.AddFilter("Microsoft.AspNetCore", LogLevel.Warning);
builder.Services.ConfigureHttpJsonOptions(json => json.SerializerOptions.PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower);
builder.Services.AddSingleton(services => services.GetRequiredService<IConfiguration>().Get<LiveOptions>() ?? new LiveOptions());
builder.Services.AddSingleton(services =>
    NpgsqlDataSource.Create(services.GetRequiredService<LiveOptions>().Database.ConnectionString));
builder.Services.AddSingleton<Feed>();

var app = builder.Build();
app.UseDefaultFiles();
app.UseStaticFiles();
// What the page draws from: everything between two moments. `until` is for looking at the past.
app.MapGet("/api/feed", async (DateTimeOffset since, DateTimeOffset? until, Feed feed, HttpContext http) =>
{
    var end = until ?? DateTimeOffset.UtcNow;
    if (end - since > FeedLimits.LongestSpan || end < since)
        return Results.BadRequest($"since and until must be in order and no more than {FeedLimits.LongestSpan.TotalMinutes} minutes apart");
    http.Response.Headers.CacheControl = "no-store";
    return Results.Ok(await feed.ReadAsync(since, end, http.RequestAborted));
});
app.MapGet("/healthz", async (Feed feed, CancellationToken cancellation) =>
    await feed.ReachableAsync(cancellation)
        ? Results.Ok("ok")
        : Results.StatusCode(StatusCodes.Status503ServiceUnavailable));
app.Run();
return 0;

/// <summary>How much may be asked for at once.</summary>
static class FeedLimits
{
    public static readonly TimeSpan LongestSpan = TimeSpan.FromMinutes(15);
}
