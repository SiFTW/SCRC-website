import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';

export default defineConfig({
  site: 'https://sussexcancer.org',
  output: 'static',
  integrations: [
    sitemap({
      filter: (page) => !page.includes('/newsletter-digest'),
    }),
  ],
  build: {
    format: 'directory',
  },
});
