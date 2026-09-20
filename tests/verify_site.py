"""Static release checks; standard library only."""
import json,re,zipfile
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urlparse,unquote
import xml.etree.ElementTree as ET
root=Path(__file__).resolve().parents[1]
class Links(HTMLParser):
 def __init__(self): super().__init__();self.links=[]
 def handle_starttag(self,tag,attrs):
  a=dict(attrs)
  for key in ['href','src']:
   if key in a:self.links.append(a[key])
errors=[]
public=list(root.glob('*.html'))+list((root/'archive').rglob('*.html'))
public=[p for p in public if not p.name.startswith('_') and p.name not in ['ad.html','wikilink_sample.html']]
for p in public:
 s=p.read_text();parser=Links();parser.feed(s)
 for link in parser.links:
  u=urlparse(link)
  if u.scheme or u.netloc or not u.path:continue
  target=((root/unquote(u.path).lstrip('/')) if u.path.startswith('/') else (p.parent/unquote(u.path))).resolve()
  if not target.exists():errors.append((str(p.relative_to(root)),link))
 assert '\ufffd' not in s,p
assert not errors,errors
home=(root/'index.html').read_text();assert len(home.encode())<30000
assert 'const QUESTIONS' not in home
assert 'adsbygoogle' not in home
assert 'google-adsense-account' in home
qfile=next((root/'assets').glob('questions-*.json'));bank=json.loads(qfile.read_text())
assert bank==json.loads((root/'index.questions.json').read_text())['questions']
assert len(bank)==40000
for p in (root/'archive').glob('20*.html'):assert 'noindex,follow' in p.read_text()
ns={'s':'http://www.sitemaps.org/schemas/sitemap/0.9'}
for loc in ET.parse(root/'sitemap.xml').findall('.//s:loc',ns):
 p=root/urlparse(loc.text).path.lstrip('/')
 if p.is_dir():p=p/'index.html'
 assert p.exists(),p
 assert 'noindex' not in p.read_text(),p
print('PASS: public local links, UTF-8, small HTML, opt-in ads, 40,000 unchanged questions, noindex/sitemap consistency')
