import { EmptyState } from "@viewallet/ui";
import { Compass } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

export function NotFoundPage() {
  const { t } = useTranslation();
  return (
    <EmptyState
      icon={<Compass aria-hidden className="size-6" />}
      title={t("notFound.title")}
      description={t("notFound.description")}
      action={
        <Link to="/" className="text-sm font-medium text-primary underline underline-offset-4">
          {t("notFound.back")}
        </Link>
      }
    />
  );
}
