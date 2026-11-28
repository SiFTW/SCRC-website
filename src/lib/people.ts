import type { CollectionEntry } from 'astro:content';

export interface FeaturedPersonEntry {
  slug: string;
  name: string;
  role: string;
}

export interface ResolvedPerson extends FeaturedPersonEntry {
  image: string;
  initials: string;
  hasProfile: boolean;
}

export function getInitials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return 'SC';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export function getCleanImage(img?: string): string {
  if (!img || img.toLowerCase().includes('photoholder')) return '';
  return img;
}

export function resolveFeaturedGroup(
  list: FeaturedPersonEntry[],
  bySlug: Map<string, CollectionEntry<'people'>>
): ResolvedPerson[] {
  return list.map((entry) => {
    const profile = bySlug.get(entry.slug);
    return {
      slug: entry.slug,
      name: entry.name,
      role: entry.role,
      image: getCleanImage(profile?.data.image),
      initials: getInitials(entry.name),
      hasProfile: Boolean(profile),
    };
  });
}
