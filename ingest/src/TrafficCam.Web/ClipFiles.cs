namespace TrafficCam.Web;

/// <summary>Serves a clip's video and still frame, found by the clip's id and nothing else.</summary>
public static class ClipFiles
{
    public static void MapClipFiles(this IEndpointRouteBuilder app)
    {
        app.MapGet("/clips/{id:guid}/video", (Guid id, ClipDirectory directory, WebOptions options, HttpContext http) =>
            SendAsync(id, clip => clip.Path, "video/mp4", directory, options, http));
        app.MapGet("/clips/{id:guid}/still", (Guid id, ClipDirectory directory, WebOptions options, HttpContext http) =>
            SendAsync(id, clip => clip.StillPath, "image/jpeg", directory, options, http));
    }

    static async Task<IResult> SendAsync(
        Guid id, Func<StoredClip, string?> file, string contentType, ClipDirectory directory, WebOptions options, HttpContext http)
    {
        var clip = await directory.FindAsync(id, http.RequestAborted);
        if (clip is null || clip.DeletedAt is not null || file(clip) is not { } recorded)
            return Results.NotFound();
        if (Within(options.ClipsRoot, recorded) is not { } path || !File.Exists(path))
            return Results.NotFound();
        // A clip's files are written once and never change.
        http.Response.Headers.CacheControl = "private, max-age=31536000, immutable";
        // Range requests are what let the browser start playing at once and seek.
        return Results.File(path, contentType, lastModified: File.GetLastWriteTimeUtc(path), enableRangeProcessing: true);
    }

    /// <summary>The full path, if it is inside the folder; null if it leads anywhere else.</summary>
    internal static string? Within(string folder, string path)
    {
        var root = Path.TrimEndingDirectorySeparator(Path.GetFullPath(folder)) + Path.DirectorySeparatorChar;
        var full = Path.GetFullPath(path);
        return full.StartsWith(root, StringComparison.Ordinal) ? full : null;
    }
}
