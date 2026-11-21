import "./styles.css";

import { createApiClient } from "@viewallet/api-client";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router";

import { Providers } from "./app/providers";
import { createRouter } from "./app/router";
import { createAdminI18n } from "./i18n";

const i18n = createAdminI18n();
const apiClient = createApiClient({ getLocale: () => i18n.language });
const router = createRouter();

const root = document.getElementById("root");
if (!root) throw new Error("#root element is missing");

createRoot(root).render(
  <StrictMode>
    <Providers i18n={i18n} apiClient={apiClient}>
      <RouterProvider router={router} />
    </Providers>
  </StrictMode>,
);
