using Microsoft.AspNetCore.Mvc.RazorPages;

namespace TrafficCam.Web.Pages;

public sealed class IndexModel(ClipDirectory directory) : PageModel
{
    public const int NewestPerType = 10;

    public IReadOnlyList<TypeColumn> Columns { get; private set; } = [];

    public async Task OnGetAsync(CancellationToken cancellation) =>
        Columns = await directory.ColumnsAsync(NewestPerType, cancellation);
}
