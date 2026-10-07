using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;

namespace TrafficCam.Web.Pages;

public sealed class TypeModel(ClipDirectory directory) : PageModel
{
    public const int PageSize = 50;
    /// <summary>How long after a quick clip was asked for the page goes on looking for it.</summary>
    static readonly TimeSpan WaitFor = TimeSpan.FromSeconds(30);

    [BindProperty(SupportsGet = true)]
    public string Type { get; set; } = "";

    /// <summary>Show entries older than this: the time of the last entry on the page before.</summary>
    [BindProperty(SupportsGet = true)]
    public DateTimeOffset? Before { get; set; }

    /// <summary>When a quick clip was asked for, if the reader has just done so.</summary>
    [BindProperty(SupportsGet = true)]
    public DateTimeOffset? Asked { get; set; }

    public IReadOnlyList<ClipEntry> Entries { get; private set; } = [];

    /// <summary>Set when there are more entries than fit on this page.</summary>
    public DateTimeOffset? Older { get; private set; }

    /// <summary>The clip asked for is not here yet, and may still come.</summary>
    public bool Waiting { get; private set; }

    /// <summary>The clip asked for did not come.</summary>
    public bool Missing { get; private set; }

    public async Task OnGetAsync(CancellationToken cancellation)
    {
        // One more than a page, to know whether there is another.
        var entries = await directory.OfTypeAsync(Type, Before, PageSize + 1, Request.IncludeArchived(), cancellation);
        Entries = [.. entries.Take(PageSize)];
        Older = entries.Count > PageSize ? Entries[^1].At : null;
        // Vision dates the clip from when the request reached it, a moment after it was made.
        if (Asked is { } asked && !Entries.Any(entry => entry.At >= asked))
        {
            Waiting = DateTimeOffset.UtcNow - asked < WaitFor;
            Missing = !Waiting;
        }
    }
}
