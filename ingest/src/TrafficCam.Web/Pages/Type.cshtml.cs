using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;

namespace TrafficCam.Web.Pages;

public sealed class TypeModel(ClipDirectory directory) : PageModel
{
    public const int PageSize = 50;

    [BindProperty(SupportsGet = true)]
    public string Type { get; set; } = "";

    /// <summary>Show entries older than this: the time of the last entry on the page before.</summary>
    [BindProperty(SupportsGet = true)]
    public DateTimeOffset? Before { get; set; }

    public IReadOnlyList<ClipEntry> Entries { get; private set; } = [];

    /// <summary>Set when there are more entries than fit on this page.</summary>
    public DateTimeOffset? Older { get; private set; }

    public async Task OnGetAsync(CancellationToken cancellation)
    {
        // One more than a page, to know whether there is another.
        var entries = await directory.OfTypeAsync(Type, Before, PageSize + 1, cancellation);
        Entries = [.. entries.Take(PageSize)];
        Older = entries.Count > PageSize ? Entries[^1].At : null;
    }
}
