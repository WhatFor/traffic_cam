using Npgsql;
using TrafficCam.Web;

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
builder.Services.AddSingleton(services => services.GetRequiredService<IConfiguration>().Get<WebOptions>() ?? new WebOptions());
builder.Services.AddSingleton(services =>
    NpgsqlDataSource.Create(services.GetRequiredService<WebOptions>().Database.ConnectionString));
builder.Services.AddSingleton<ClipDirectory>();
builder.Services.AddRazorPages();

var app = builder.Build();
app.UseStaticFiles();
app.MapRazorPages();
app.MapClipFiles();
app.MapGet("/healthz", async (ClipDirectory directory, CancellationToken cancellation) =>
    await directory.ReachableAsync(cancellation)
        ? Results.Ok("ok")
        : Results.StatusCode(StatusCodes.Status503ServiceUnavailable));
app.Run();
return 0;
