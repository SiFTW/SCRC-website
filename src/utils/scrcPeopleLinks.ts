import type { CollectionEntry } from 'astro:content';

export interface PersonRecord {
  slug: string;
  name: string;
  cleanName: string;
  priority: number;
}

export interface PeopleNameIndex {
  bySlug: Map<string, PersonRecord>;
  byVariant: Map<string, PersonRecord>;
  pattern: RegExp | null;
}

const NICKNAME_MAP: Record<string, string[]> = {
  timothy: ['tim'],
  antony: ['tony'],
  anthony: ['tony'],
  melanie: ['mel'],
  christopher: ['chris'],
  matthew: ['matt'],
  stephen: ['steve'],
  elizabeth: ['liz', 'beth'],
  rebecca: ['becky'],
  penelope: ['penny'],
  oliver: ['ollie'],
  valerie: ['val'],
  jonathan: ['jon'],
  michael: ['mike'],
  nicholas: ['nick'],
  susan: ['sue'],
  william: ['will', 'bill'],
  alexander: ['alex'],
  benjamin: ['ben'],
  katherine: ['kate', 'katie'],
  jennifer: ['jenny'],
  jessica: ['jess'],
  joanne: ['jo'],
  joanna: ['jo'],
  daniel: ['dan'],
  david: ['dave'],
  robert: ['rob'],
  edward: ['ed'],
  andrew: ['andy'],
};

function stripHonorifics(name: string): string {
  return name
    .replace(/^(?:(?:prof(?:essor)?\.?|dr\.?|dame|sir|mr\.?|mrs\.?|ms\.?)\s+)+/i, '')
    .trim();
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function escapeHtmlAttr(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function normalizeWhitespace(s: string): string {
  return s.replace(/&nbsp;/gi, ' ').replace(/\s+/g, ' ').trim();
}

/**
 * Build a fast, collision-safe name index across all SCRC people profiles.
 * Prefers profiles with photos, leadership/management flags, or ORCIDs if two slugs share a variant.
 */
export function buildPeopleNameIndex(people: CollectionEntry<'people'>[]): PeopleNameIndex {
  const bySlug = new Map<string, PersonRecord>();
  const byVariant = new Map<string, PersonRecord>();

  for (const person of people) {
    const rawName = (person.data.name || '').trim();
    if (!rawName) continue;
    const cleanName = stripHonorifics(rawName);
    const parts = cleanName.split(/\s+/).filter(Boolean);
    if (parts.length < 2) continue;

    const hasRealPhoto =
      Boolean(person.data.image) && !person.data.image!.toLowerCase().includes('photoholder');
    const priority =
      (person.data.managementGroup ? 10 : 0) +
      (hasRealPhoto ? 5 : 0) +
      (person.data.orcid ? 3 : 0);

    const record: PersonRecord = {
      slug: person.id,
      name: rawName,
      cleanName,
      priority,
    };
    bySlug.set(person.id, record);

    const variants = new Set<string>([cleanName]);
    // Also register "First Last" when middle names/initials exist
    if (parts.length >= 3) {
      variants.add(`${parts[0]} ${parts[parts.length - 1]}`);
    }
    // Register common UK academic nicknames (e.g. Tim Humphrey, Tony Carr, Mel Flint)
    const firstLower = parts[0].toLowerCase();
    for (const nick of NICKNAME_MAP[firstLower] || []) {
      variants.add(`${nick} ${parts[parts.length - 1]}`);
    }

    for (const v of variants) {
      const key = v.toLowerCase();
      if (key.length < 6) continue;
      const existing = byVariant.get(key);
      if (!existing || record.priority > existing.priority) {
        byVariant.set(key, record);
      }
    }
  }

  const sortedVariants = Array.from(byVariant.keys()).sort((a, b) => b.length - a.length);
  if (sortedVariants.length === 0) {
    return { bySlug, byVariant, pattern: null };
  }

  // Allow regular spaces or &nbsp; between words inside a matched name
  const nameAlternation = sortedVariants
    .map((v) =>
      v
        .split(' ')
        .map((token) => escapeRegExp(token))
        .join('(?:\\s|&nbsp;)+')
    )
    .join('|');

  // Optional academic honorific prefix right before the matched name (e.g. "Dr Rhys Morgan", "Professor Dame Lesley Fallowfield")
  const honorificPrefix = '(?:(?:Prof(?:essor)?\\.?|Dr\\.?|Dame|Sir)(?:\\s|&nbsp;)+){0,2}';
  const pattern = new RegExp(
    `(?<![A-Za-z])(${honorificPrefix}(?:${nameAlternation}))(?![A-Za-z])`,
    'gi'
  );

  return { bySlug, byVariant, pattern };
}

/**
 * Look up an exact SCRC member by display name (e.g., for news post author bylines).
 */
export function findPersonByName(name: string | undefined, index: PeopleNameIndex): PersonRecord | null {
  if (!name) return null;
  const clean = stripHonorifics(normalizeWhitespace(name)).toLowerCase();
  if (!clean) return null;
  return index.byVariant.get(clean) || null;
}

/**
 * Resolve a regex match (which may include an honorific prefix or &nbsp;) back to its PersonRecord.
 */
function resolveMatchedPerson(matchedText: string, index: PeopleNameIndex): PersonRecord | null {
  const normalized = stripHonorifics(normalizeWhitespace(matchedText)).toLowerCase();
  return index.byVariant.get(normalized) || null;
}

/**
 * Safely link all SCRC member mentions in an HTML string without modifying HTML tags,
 * attributes, or content inside existing <a>, <script>, <style>, <code>, or <pre> elements.
 */
export function linkScrcPeopleInHtml(
  html: string,
  index: PeopleNameIndex
): { html: string; mentionedSlugs: string[] } {
  if (!html || !index.pattern) {
    return { html: html || '', mentionedSlugs: [] };
  }

  const mentionedOrder: string[] = [];
  const seenSlugs = new Set<string>();

  // Split into HTML tags vs text segments
  const tokens = html.split(/(<[^>]+>)/g);
  let skipDepth = 0;

  const out = tokens.map((token) => {
    if (!token) return '';

    if (token.startsWith('<') && token.endsWith('>')) {
      const openMatch = token.match(/^<(a|script|style|code|pre)\b/i);
      const closeMatch = token.match(/^<\/(a|script|style|code|pre)\b/i);
      if (openMatch && !token.endsWith('/>')) {
        skipDepth += 1;
      } else if (closeMatch && skipDepth > 0) {
        skipDepth -= 1;
      }
      return token;
    }

    if (skipDepth > 0) {
      return token;
    }

    index.pattern!.lastIndex = 0;
    return token.replace(index.pattern!, (fullMatch) => {
      const person = resolveMatchedPerson(fullMatch, index);
      if (!person) return fullMatch;
      if (!seenSlugs.has(person.slug)) {
        seenSlugs.add(person.slug);
        mentionedOrder.push(person.slug);
      }
      return `<a href="/directory/${person.slug}" class="scrc-author-link" title="View ${escapeHtmlAttr(person.name)}'s SCRC profile">${fullMatch}</a>`;
    });
  });

  return {
    html: out.join(''),
    mentionedSlugs: mentionedOrder,
  };
}

/**
 * Find all SCRC member slugs mentioned in a news post's title or body (excluding the author byline
 * so site editors are only linked to articles that actually feature/mention them).
 */
export function findMentionedPeopleInNews(
  post: CollectionEntry<'news'>,
  index: PeopleNameIndex
): PersonRecord[] {
  if (!index.pattern) return [];

  const bodyHtml = post.rendered?.html || post.body || '';
  const plainBody = bodyHtml.replace(/<[^>]+>/g, ' ');
  const combined = `${post.data.title || ''}\n${plainBody}`;

  const seen = new Set<string>();
  const results: PersonRecord[] = [];
  index.pattern.lastIndex = 0;

  let match: RegExpExecArray | null;
  while ((match = index.pattern.exec(combined)) !== null) {
    const person = resolveMatchedPerson(match[1], index);
    if (person && !seen.has(person.slug)) {
      seen.add(person.slug);
      results.push(person);
    }
  }

  return results;
}
