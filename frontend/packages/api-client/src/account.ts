/**
 * Account hub (e-mail change), transaction categories, CSV exports, help center and support
 * tickets.
 */
import { useQuery } from "@tanstack/react-query";

import { type ApiClient, type Schemas, unwrap } from "./client";
import { useApiClient } from "./context";

export type TransactionCategory = Schemas["CategoryOut"];
export type ExportJob = Schemas["ExportOut"];
export type HelpArticleSummary = Schemas["ArticleSummaryOut"];
export type HelpArticle = Schemas["ArticleOut"];
export type Ticket = Schemas["TicketOut"];
export type TicketDetail = Schemas["TicketDetailOut"];
export type TicketInput = Schemas["TicketIn"];

export const accountKeys = {
  categories: ["transactions", "categories"] as const,
  export: (id: string) => ["transactions", "export", id] as const,
  help: (locale: string, q: string) => ["help", locale, q] as const,
  article: (locale: string, slug: string) => ["help", locale, "article", slug] as const,
  tickets: ["support", "tickets"] as const,
  ticket: (reference: string) => ["support", "ticket", reference] as const,
};

export const accountApi = (client: ApiClient) => ({
  requestEmailChange: async (password: string, newEmail: string) =>
    unwrap(await client.POST("/api/v1/me/email", { body: { password, new_email: newEmail } })),
  confirmEmailChange: async (code: string) =>
    unwrap(await client.POST("/api/v1/me/email/confirm", { body: { code } })),
  categories: async () => unwrap(await client.GET("/api/v1/transactions/categories")),
  setCategory: async (reference: string, categoryCode: string) =>
    unwrap(
      await client.PATCH("/api/v1/transactions/{reference}/category", {
        params: { path: { reference } },
        body: { category_code: categoryCode },
      }),
    ),
  createExport: async (dateFrom: string, dateTo: string, idempotencyKey: string) =>
    unwrap(
      await client.POST("/api/v1/transactions/exports", {
        params: { header: { "Idempotency-Key": idempotencyKey } },
        body: { date_from: dateFrom, date_to: dateTo },
      }),
    ),
  export: async (id: string) =>
    unwrap(
      await client.GET("/api/v1/transactions/exports/{export_id}", {
        params: { path: { export_id: id } },
      }),
    ),
  helpArticles: async (locale: "vi" | "en", q: string) =>
    unwrap(
      await client.GET("/api/v1/help/articles", {
        params: { query: { locale, ...(q ? { q } : {}) } },
      }),
    ),
  helpArticle: async (locale: "vi" | "en", slug: string) =>
    unwrap(
      await client.GET("/api/v1/help/articles/{slug}", {
        params: { path: { slug }, query: { locale } },
      }),
    ),
  tickets: async () => unwrap(await client.GET("/api/v1/support/tickets")),
  ticket: async (reference: string) =>
    unwrap(
      await client.GET("/api/v1/support/tickets/{reference}", {
        params: { path: { reference } },
      }),
    ),
  openTicket: async (body: TicketInput) =>
    unwrap(await client.POST("/api/v1/support/tickets", { body })),
  replyTicket: async (reference: string, body: string) =>
    unwrap(
      await client.POST("/api/v1/support/tickets/{reference}/messages", {
        params: { path: { reference } },
        body: { body },
      }),
    ),
});

export function useAccountApi() {
  return accountApi(useApiClient());
}

export function useCategories() {
  const api = useAccountApi();
  return useQuery({
    queryKey: accountKeys.categories,
    queryFn: api.categories,
    staleTime: 600_000,
  });
}

/** Polls every 2 s while the file is being built. */
export function useExportJob(id: string | null) {
  const api = useAccountApi();
  return useQuery({
    queryKey: accountKeys.export(id ?? ""),
    queryFn: () => api.export(id ?? ""),
    enabled: id !== null,
    refetchInterval: (query) =>
      query.state.data?.status === "PENDING" && query.state.dataUpdateCount < 60 ? 2_000 : false,
  });
}

export function useHelpArticles(locale: "vi" | "en", q: string) {
  const api = useAccountApi();
  return useQuery({
    queryKey: accountKeys.help(locale, q),
    queryFn: () => api.helpArticles(locale, q),
    staleTime: 300_000,
  });
}

export function useHelpArticle(locale: "vi" | "en", slug: string) {
  const api = useAccountApi();
  return useQuery({
    queryKey: accountKeys.article(locale, slug),
    queryFn: () => api.helpArticle(locale, slug),
    staleTime: 300_000,
  });
}

export function useTickets() {
  const api = useAccountApi();
  return useQuery({ queryKey: accountKeys.tickets, queryFn: api.tickets });
}

export function useTicket(reference: string) {
  const api = useAccountApi();
  return useQuery({
    queryKey: accountKeys.ticket(reference),
    queryFn: () => api.ticket(reference),
  });
}
