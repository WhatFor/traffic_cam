using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;

namespace TrafficCam.Web.Pages;

/// <summary>The front page. The buttons in every page's header post here.</summary>
public sealed class IndexModel(ClipDirectory directory, VisionLink vision) : PageModel
{
    public const int NewestPerType = 10;

    public IReadOnlyList<TypeColumn> Columns { get; private set; } = [];

    public async Task OnGetAsync(CancellationToken cancellation) =>
        Columns = await directory.ColumnsAsync(NewestPerType, Request.Listed(), cancellation);

    /// <summary>Asks vision for a clip of what has just happened, and goes to where it will be listed.</summary>
    public async Task<IActionResult> OnPostQuickClipAsync(CancellationToken cancellation)
    {
        var asked = DateTimeOffset.UtcNow;
        if (!await vision.QuickClipAsync(cancellation))
            return VisionLink.Unreachable();
        return Redirect($"/types/{Wording.Manual}?asked={Uri.EscapeDataString(asked.UtcDateTime.ToString("o"))}");
    }

    public IActionResult OnPostArchived(bool include, string? returnUrl) =>
        Include(Preferences.ArchivedCookie, include, returnUrl);

    public IActionResult OnPostViewed(bool include, string? returnUrl) =>
        Include(Preferences.ViewedCookie, include, returnUrl);

    LocalRedirectResult Include(string cookie, bool include, string? returnUrl)
    {
        Response.Include(cookie, include);
        return LocalRedirect(Url.IsLocalUrl(returnUrl) ? returnUrl : "/");
    }
}
