"""Package only public website files, not scripts, backups or data dumps."""
from pathlib import Path
import re
import zipfile
from build_archive import STATIC_PAGES


def package(destination=None):
    root = Path(__file__).resolve().parent
    destination = Path(destination) if destination else root.parent / 'WikiLinkGame-release-20260921.zip'
    public = {Path(p or 'index.html') for p in STATIC_PAGES}
    public.update(Path(p) for p in ['404.html', 'ads.txt', 'robots.txt', 'sitemap.xml', 'logo_4_dark.svg', 'logo_4_dark.png'])
    public.update(p.relative_to(root) for p in (root / 'archive').rglob('*.html'))
    # Follow only the current content-addressed loader and its asset references.
    pending = [root / 'index.html']
    visited = set()
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        for asset in re.findall(r'assets/[A-Za-z0-9_-]+\.(?:json|js)', path.read_text()):
            public.add(Path(asset))
            if asset.endswith('.js'):
                pending.append(root / asset)
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(public):
            archive.write(root / path, path.as_posix())
    print(f'{destination}: {len(public)} public files, {destination.stat().st_size:,} bytes')

if __name__ == '__main__':
    package()
