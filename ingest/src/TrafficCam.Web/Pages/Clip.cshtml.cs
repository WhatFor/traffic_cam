using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;

namespace TrafficCam.Web.Pages;

public sealed class ClipModel(ClipDirectory directory) : PageModel
{
    /// <summary>How long before a trigger's moment a link from a list starts the clip.</summary>
    public const double LeadInSeconds = 2;

    [BindProperty(SupportsGet = true)]
    public Guid Id { get; set; }

    /// <summary>Seconds into the clip to start playing from.</summary>
    [BindProperty(SupportsGet = true, Name = "t")]
    public double From { get; set; }

    public StoredClip Clip { get; private set; } = null!;
    public IReadOnlyList<ClipEntry> Triggers { get; private set; } = [];

    public async Task<IActionResult> OnGetAsync(CancellationToken cancellation)
    {
        if (await directory.FindAsync(Id, cancellation) is not { } clip)
            return NotFound();
        Clip = clip;
        Triggers = await directory.TriggersAsync(Id, cancellation);
        From = Math.Clamp(From, 0, clip.LengthSeconds);
        if (clip.DeletedAt is not null)
            Response.StatusCode = StatusCodes.Status404NotFound;
        return Page();
    }
}
