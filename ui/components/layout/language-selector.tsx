"use client";
// NOTE  MC8yOmFIVnBZMlhscm9ua3VMazZTRU5ZVmc9PTplYTcyOGRiNw==

import * as React from "react";
import { Languages } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useLanguage } from "@/providers/LanguageProvider";
import { Language, languageNames } from "@/lib/translations";
// eslint-disable  MS8yOmFIVnBZMlhscm9ua3VMazZTRU5ZVmc9PTplYTcyOGRiNw==

export function LanguageSelector() {
  const { language, setLanguage } = useLanguage();

  const languages: Language[] = ["zh", "en", "ja"];

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" title={languageNames[language]}>
          <Languages className="h-5 w-5" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {languages.map((lang) => (
          <DropdownMenuItem
            key={lang}
            onClick={() => setLanguage(lang)}
            className={language === lang ? "bg-accent" : ""}
          >
            {languageNames[lang]}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

