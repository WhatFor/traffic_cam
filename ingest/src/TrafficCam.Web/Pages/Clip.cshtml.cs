using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;

namespace TrafficCam.Web.Pages;

public sealed class ClipModel(ClipDirectory directory, ClipMarks marks, VisionLink vision) : PageModel
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

    /// <summary>Opening a clip is what marks it viewed.</summary>
    public async Task<IActionResult> OnGetAsync(CancellationToken cancellation)
    {
        if (await directory.FindAsync(Id, cancellation) is not { } clip)
            return NotFound();
        Clip = clip;
        Triggers = await directory.TriggersAsync(Id, cancellation);
        From = Math.Clamp(From, 0, clip.LengthSeconds);
        if (clip.DeletedAt is not null)
            Response.StatusCode = StatusCodes.Status404NotFound;
        else if (!clip.Viewed)
            await marks.ViewedAsync(Id, cancellation);
        return Page();
    }

    public async Task<IActionResult> OnPostArchiveAsync(bool on, CancellationToken cancellation)
    {
        if (!await ExistsAsync(cancellation))
            return NotFound();
        await marks.ArchiveAsync(Id, on, cancellation);
        return Back();
    }

    public async Task<IActionResult> OnPostFalsePositiveAsync(bool on, CancellationToken cancellation)
    {
        if (!await ExistsAsync(cancellation))
            return NotFound();
        // Vision first: the mark must not say the files are being kept when vision was never told.
        if (!await vision.KeepAsync(Id, on, cancellation))
            return VisionLink.Unreachable();
        await marks.FalsePositiveAsync(Id, on, cancellation);
        return Back();
    }

    public async Task<IActionResult> OnPostDescribeAsync(string? description, CancellationToken cancellation)
    {
        if (!await ExistsAsync(cancellation))
            return NotFound();
        await marks.DescribeAsync(Id, description, cancellation);
        return Back();
    }

    async Task<bool> ExistsAsync(CancellationToken cancellation) =>
        await directory.FindAsync(Id, cancellation) is { DeletedAt: null };

    RedirectResult Back() => Redirect($"/clips/{Id}");
}
