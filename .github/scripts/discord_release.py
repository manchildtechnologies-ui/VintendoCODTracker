"""Post a published release to Discord; webhook is supplied only via a secret."""
import json
import os
import re
import urllib.parse
import urllib.request


def github(path):
    request = urllib.request.Request(
        'https://api.github.com/repos/' + os.environ['GITHUB_REPOSITORY'] + path,
        headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
                 'Accept': 'application/vnd.github+json', 'User-Agent': 'Vintendo-Releases'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def build_payload(release, notes):
    url = release['html_url']
    notes = notes.strip() or 'See the full release notes for this version.'
    if len(notes) > 3400:
        notes = notes[:3400].rsplit('\n', 1)[0] + '\n…'
    downloads = '\n'.join(
        f"[⬇ {asset['name'][:100]}]({asset['browser_download_url']})"
        for asset in release.get('assets', [])[:5])
    if len(downloads) > 1000:
        downloads = f'[Download this version]({url})'
    return {
        'username': 'Vintendo • Release Updates',
        'avatar_url': 'https://github.com/manchildtechnologies-ui.png',
        'content': '@everyone 🚀 **A new VintendoCODTracker update is available!**',
        'allowed_mentions': {'parse': ['everyone']},
        'embeds': [{
            'title': ('🎮 VintendoCODTracker — ' + release['tag_name'])[:256],
            'url': url, 'color': 0x8B5CF6,
            'description': '### ✨ What’s new\n' + notes + f'\n\n[Read the full changelog →]({url})',
            'fields': [
                {'name': '📦 Downloads', 'value': downloads or f'[Get the update]({url})'},
                {'name': '🏷 Version', 'value': release['tag_name'][:100], 'inline': True},
                {'name': '📣 Channel', 'value': 'Preview' if release.get('prerelease') else 'Stable', 'inline': True}],
            'footer': {'text': 'VintendoCODTracker • Official release news'},
            'timestamp': release['published_at']}]
    }


def main():
    tag = os.environ.get('RELEASE_TAG', '')
    release = github('/releases/tags/' + urllib.parse.quote(tag, safe='') if tag else '/releases/latest')
    if release.get('draft'):
        raise RuntimeError('Draft releases cannot be announced.')
    notes = release.get('body') or ''
    # GitHub-generated notes can contain only a comparison link for direct commits.
    meaningful = re.sub(r'\*\*Full Changelog\*\*:.*', '', notes).strip()
    if not meaningful:
        releases = github('/releases?per_page=100')
        previous = next((r for r in releases if not r['draft'] and
                         r['published_at'] < release['published_at'] and not r['prerelease']), None)
        if previous:
            base = urllib.parse.quote(previous['tag_name'], safe='')
            head = urllib.parse.quote(release['tag_name'], safe='')
            comparison = github('/compare/' + base + '...' + head)
            messages = [c['commit']['message'].splitlines()[0] for c in comparison['commits']]
            notes = '\n'.join('• ' + message for message in messages[:20]) + '\n\n' + notes
    payload = build_payload(release, notes)
    webhook = os.environ['DISCORD_WEBHOOK_URL'].rstrip('/')
    request = urllib.request.Request(webhook + '?wait=true', data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json', 'User-Agent': 'Vintendo-Releases'}, method='POST')
    with urllib.request.urlopen(request, timeout=30) as response:
        message = json.load(response)
    print('Discord announcement sent: ' + message['id'])
    print('Everyone mention active: ' + str(message.get('mention_everyone', False)))


if __name__ == '__main__':
    main()
