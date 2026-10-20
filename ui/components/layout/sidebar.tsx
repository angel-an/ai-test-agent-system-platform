// TODO  MC80OmFIVnBZMlhscm9ua3VMazZRekZUY3c9PTpiNTBjOWYwMg==

"use client";
// NOTE  MS80OmFIVnBZMlhscm9ua3VMazZRekZUY3c9PTpiNTBjOWYwMg==

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  FolderKanban,
  FileText,
  PlayCircle,
  ClipboardList,
  BarChart3,
  ChevronDown,
  Settings,
  Home,
  Database,
  Globe,
  CalendarClock,
  Code2,
  Layers,
  Shield,
  Smartphone,
  TabletSmartphone,
  Route,
  BookOpen,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { ProjectInfo } from "@/lib/api/types";
import { useLanguage } from "@/providers/LanguageProvider";

interface SidebarProps {
  projects: ProjectInfo[];
  currentProject?: ProjectInfo | null;
  onProjectChange?: (project: ProjectInfo | null) => void;
}
// FIXME  Mi80OmFIVnBZMlhscm9ua3VMazZRekZUY3c9PTpiNTBjOWYwMg==

export function Sidebar({
  projects,
  currentProject,
  onProjectChange,
}: SidebarProps) {
  const pathname = usePathname();
  const { t } = useLanguage();

  const navItems = [
    {
      title: t("nav.testCases"),
      href: "/test-cases",
      icon: FileText,
    },
    {
      title: t("nav.apiTests"),
      href: "/api-tests",
      icon: Database,
    },
    {
      title: t("nav.webTests"),
      href: "/web-tests",
      icon: Globe,
    },
    {
      title: "路径配置",
      href: "/web-path-templates",
      icon: Route,
    },
    {
      title: t("nav.androidTests"),
      href: "/android-tests",
      icon: Smartphone,
    },
    {
      title: "IOS测试",
      href: "/ios-tests",
      icon: TabletSmartphone,
    },
    {
      title: t("nav.pentest"),
      href: "/security-tests",
      icon: Shield,
    },
    {
      title: "知识库",
      href: "/knowledge-base",
      icon: BookOpen,
    },
    {
      title: t("nav.testRuns"),
      href: "/test-runs",
      icon: PlayCircle,
    },
    {
      title: t("nav.reports"),
      href: "/reports",
      icon: BarChart3,
    },
    // {
    //   title: t("nav.codeAnalysis"),
    //   href: "/code-analysis",
    //   icon: Code2,
    // },
    {
      title: t("nav.fullstackAnalysis"),
      href: "/fullstack-analysis",
      icon: Layers,
    },
  ];

  const getNavHref = (baseHref: string) => {
    if (!currentProject) return "#";
    return `/projects/${currentProject.identifier}${baseHref}`;
  };

  const isActive = (href: string) => {
    if (!currentProject) return false;
    const fullHref = getNavHref(href);
    return pathname.startsWith(fullHref);
  };

  const handleProjectChange = (project: ProjectInfo | null) => {
    if (project) {
      onProjectChange?.(project);
    } else {
      // 选择"所有项目"时，导航到项目列表页
      onProjectChange?.(null as any);
    }
  };

  return (
    <div className="flex h-full w-60 flex-col border-r bg-card">
      {/* Logo */}
      <div className="flex h-14 items-center border-b px-4">
        <Link href="/projects" className="flex items-center gap-2">
          <img src="/logo.svg" alt="logo" className="h-6 w-6" />
          <span className="font-semibold">{t("meta.title")}</span>
        </Link>
      </div>

      {/* Project Selector */}
      <div className="border-b p-3">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="outline"
              className="w-full justify-between"
              disabled={projects.length === 0}
            >
              <span className="truncate">
                {currentProject?.name || t("nav.allProjects")}
              </span>
              <ChevronDown className="h-4 w-4 shrink-0 opacity-50" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent className="w-52">
            {/* 所有项目选项 */}
            <DropdownMenuItem
              onClick={() => handleProjectChange(null)}
              className={cn(!currentProject && "bg-accent")}
            >
              <Home className="mr-2 h-4 w-4" />
              <span>{t("nav.allProjects")}</span>
            </DropdownMenuItem>
            {/* 分隔线 */}
            <div className="my-1 h-px bg-border" />
            {/* 项目列表 */}
            {projects.map((project) => (
              <DropdownMenuItem
                key={project.identifier}
                onClick={() => onProjectChange?.(project)}
                className={cn(
                  currentProject?.identifier === project.identifier &&
                    "bg-accent"
                )}
              >
                <span className="truncate">{project.name}</span>
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {/* Navigation */}
      <ScrollArea className="flex-1 px-3 py-2">
        <nav className="flex flex-col gap-1">
          {/* 选择项目后，显示项目导航 */}
          {currentProject && (
            <>
              <div className="mb-2 px-2 text-xs font-medium text-muted-foreground">
                {t("nav.projectNavigation")}
              </div>
              {navItems.map((item) => (
                <Link
                  key={item.href}
                  href={getNavHref(item.href)}
                  prefetch={true}
                >
                  <Button
                    variant={isActive(item.href) ? "secondary" : "ghost"}
                    className={cn("w-full justify-start", (item as any).indent && "pl-8 text-sm")}
                  >
                    <item.icon className="mr-2 h-4 w-4" />
                    {item.title}
                  </Button>
                </Link>
              ))}
            </>
          )}
        </nav>
      </ScrollArea>

      {/* Footer */}
      <div className="border-t p-3">
        <Button variant="ghost" className="w-full justify-start">
          <Settings className="mr-2 h-4 w-4" />
          {t("nav.settings")}
        </Button>
      </div>
    </div>
  );
}
// FIXME  My80OmFIVnBZMlhscm9ua3VMazZRekZUY3c9PTpiNTBjOWYwMg==
