FROM node:22-alpine AS build
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
# The landing page's pilot-enquiry address, baked in at build time (docker build --build-arg ...). Empty: no button.
ARG NEXT_PUBLIC_CONTACT_EMAIL=""
ENV NEXT_PUBLIC_CONTACT_EMAIL=$NEXT_PUBLIC_CONTACT_EMAIL
# Interface language (en-GB or fr-FR) and the time zone times are shown in; also baked in at build time.
ARG NEXT_PUBLIC_LOCALE="en-GB"
ENV NEXT_PUBLIC_LOCALE=$NEXT_PUBLIC_LOCALE
ARG NEXT_PUBLIC_TIME_ZONE=""
ENV NEXT_PUBLIC_TIME_ZONE=$NEXT_PUBLIC_TIME_ZONE
RUN pnpm build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production
# The app only runs `node server.js`: drop the package managers the base image ships. They are never used at run
# time, and npm's own bundled packages carry the image's only HIGH vulnerabilities (Trivy).
RUN rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack /opt/yarn-* \
      /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack /usr/local/bin/yarn /usr/local/bin/yarnpkg
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
