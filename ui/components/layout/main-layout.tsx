// eslint-disable  MC80OmFIVnBZMlhscm9ua3VMazZOMk5pVnc9PTo1Y2VhNGI1OQ==

"use client";
// NOTE  MS80OmFIVnBZMlhscm9ua3VMazZOMk5pVnc9PTo1Y2VhNGI1OQ==

import * as React from "react";
import { useRouter, usePathname } from "next/navigation";
import { Sidebar } from "./sidebar";
import { Header } from "./header";
import { getProjects } from "@/lib/api/projects";
import { useAuth } from "@/providers/AuthProvider";
import type { ProjectInfo } from "@/lib/api/types";

interface MainLayoutProps {
  children: React.ReactNode;
  title?: string;
  headerContent?: React.ReactNode;
}
// NOTE  Mi80OmFIVnBZMlhscm9ua3VMazZOMk5pVnc9PTo1Y2VhNGI1OQ==

export function MainLayout({ children, title, headerContent }: MainLayoutProps) {
  const router = useRouter();
  const pathname = usePathname();
  const { isAuthenticated, isLoading: authLoading } = useAuth();
  const [projects, setProjects] = React.useState<ProjectInfo[]>([]);
  const [currentProject, setCurrentProject] = React.useState<ProjectInfo | null>(
    null
  );
  const [loading, setLoading] = React.useState(true);

  // 路由守卫：未登录用户重定向到登录页
  // 注意：后端 API 使用 CurrentUserIdDep（默认用户ID），不强制认证
  // 但为了用户体验，仍要求登录后才能访问非登录页面
  React.useEffect(() => {
    // 只在认证状态确定后才执行重定向
    if (!authLoading && !isAuthenticated && pathname !== "/login") {
      // 延迟重定向，避免闪烁
      const timer = setTimeout(() => {
        router.push("/login");
      }, 100);
      return () => clearTimeout(timer);
    }
  }, [authLoading, isAuthenticated, pathname, router]);

  // 从 URL 中提取项目 ID
  const projectIdFromUrl = React.useMemo(() => {
    const match = pathname.match(/\/projects\/([^/]+)/);
    return match ? match[1] : null;
  }, [pathname]);

  // 加载项目列表 - 项目ID变化时重新加载当前项目信息
  React.useEffect(() => {
    const loadProjects = async () => {
      try {
        const response = await getProjects({ page_size: 100 });
        if (response.success && response.data) {
          setProjects(response.data);

          // 如果 URL 中有项目 ID，设置当前项目
          if (projectIdFromUrl) {
            const project = response.data.find(
              (p) => p.identifier === projectIdFromUrl
            );
            if (project) {
              setCurrentProject(project);
            }
          }
        }
      } catch (error) {
        console.error("Failed to load projects:", error);
      } finally {
        setLoading(false);
      }
    };

    loadProjects();
  }, [projectIdFromUrl]); // 监听 URL 中的项目 ID 变化，切换项目时更新当前项目

  // 处理项目切换
  const handleProjectChange = (project: ProjectInfo | null) => {
    if (project) {
      setCurrentProject(project);
      // 导航到新项目的测试用例页面
      router.push(`/projects/${project.identifier}/test-cases`);
    } else {
      setCurrentProject(null);
      // 导航到项目列表页
      router.push("/projects");
    }
  };

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar
        projects={projects}
        currentProject={currentProject}
        onProjectChange={handleProjectChange}
      />
      <div className="flex flex-1 flex-col overflow-hidden">
        <Header title={title}>{headerContent}</Header>
        <main className="flex-1 overflow-auto bg-muted/30 p-6">{children}</main>
      </div>
    </div>
  );
}
// eslint-disable  My80OmFIVnBZMlhscm9ua3VMazZOMk5pVnc9PTo1Y2VhNGI1OQ==

