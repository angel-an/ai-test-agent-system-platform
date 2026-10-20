/**
 * andan
 */
"use client";
// TODO  MC8yOmFIVnBZMlhscm9ua3VMazZVV1pZVHc9PTozYWI3NDc1ZQ==

import { MainLayout } from "@/components/layout/main-layout";
import { useLanguage } from "@/providers/LanguageProvider";
// eslint-disable  MS8yOmFIVnBZMlhscm9ua3VMazZVV1pZVHc9PTozYWI3NDc1ZQ==

export default function FullstackAnalysisPage() {
  const { t } = useLanguage();

  return (
    <MainLayout title={t("nav.fullstackAnalysis")}>
      <div className="-m-6 h-full">
        <iframe
          src="/gitnexus-web/index.html"
          className="h-full w-full border-0"
          title={t("nav.fullstackAnalysis")}
          allow="fullscreen"
        />
      </div>
    </MainLayout>
  );
}
