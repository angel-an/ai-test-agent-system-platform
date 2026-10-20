"use client";

/**
 * Web测试可视化报告 - 演示/测试数据
 *
 * 提供模拟的Web测试报告数据，用于演示可视化报告组件的效果
 */

import { VisualReportDialog } from "./visual-report-dashboard";
import type { WebTestReport } from "./visual-report-dashboard";

// ==============================================================================
// 模拟报告数据 - 小杨生煎 B端储值充值 测试结果
// ==============================================================================

export const mockXYSJGReport: WebTestReport = {
  totalTests: 15,
  passed: 12,
  failed: 2,
  skipped: 1,
  timedOut: 0,
  totalDuration: 125000,
  startTime: "2026-08-25T09:00:00Z",
  endTime: "2026-08-25T09:02:05Z",
  browser: "Chromium",
  viewport: "1280x1800",
  suites: [
    {
      title: "阶段1: 登录与导航",
      totalDuration: 45000,
      tests: [
        {
          title: "TC-001: 导航到登录页面，页面正常加载",
          status: "passed",
          duration: 3200,
          errors: [],
          steps: [
            { title: "导航到登录页面", status: "passed", duration: 1500 },
            { title: "验证页面元素", status: "passed", duration: 1700 },
          ],
        },
        {
          title: "TC-002: 输入账号和密码",
          status: "passed",
          duration: 2100,
          errors: [],
          steps: [
            { title: "输入账号 andan", status: "passed", duration: 800 },
            { title: "输入密码", status: "passed", duration: 600 },
            { title: "验证输入", status: "passed", duration: 700 },
          ],
        },
        {
          title: "TC-003: 登录成功，跳转到门户中心页面",
          status: "passed",
          duration: 8500,
          errors: [],
          steps: [
            { title: "点击登录按钮", status: "passed", duration: 500 },
            { title: "等待页面跳转", status: "passed", duration: 6000 },
            { title: "验证门户中心", status: "passed", duration: 2000 },
          ],
        },
        {
          title: "TC-004: 点击数盈·营销云卡片",
          status: "passed",
          duration: 5200,
          errors: [],
          steps: [
            { title: "查找营销云卡片", status: "passed", duration: 1200 },
            { title: "点击卡片", status: "passed", duration: 800 },
            { title: "验证页面跳转", status: "passed", duration: 3200 },
          ],
        },
        {
          title: "TC-005: 进入营销云系统，页面加载完成",
          status: "passed",
          duration: 3800,
          errors: [],
          steps: [
            { title: "验证URL", status: "passed", duration: 500 },
            { title: "验证页面内容", status: "passed", duration: 3300 },
          ],
        },
        {
          title: "TC-006: 点击忠诚度菜单，菜单展开",
          status: "passed",
          duration: 2900,
          errors: [],
          steps: [
            { title: "查找忠诚度菜单", status: "passed", duration: 800 },
            { title: "点击菜单", status: "passed", duration: 600 },
            { title: "验证子菜单展开", status: "passed", duration: 1500 },
          ],
        },
        {
          title: "TC-007: 点击储值管理子菜单",
          status: "passed",
          duration: 2200,
          errors: [],
          steps: [
            { title: "点击储值管理", status: "passed", duration: 700 },
            { title: "验证子菜单显示", status: "passed", duration: 1500 },
          ],
        },
        {
          title: "TC-008: 点击储值账户列表，页面加载完成",
          status: "passed",
          duration: 5600,
          errors: [],
          steps: [
            { title: "点击储值账户列表", status: "passed", duration: 800 },
            { title: "等待页面加载", status: "passed", duration: 3500 },
            { title: "验证页面内容", status: "passed", duration: 1300 },
          ],
        },
      ],
    },
    {
      title: "阶段2: 储值账户列表页面检查",
      totalDuration: 8200,
      tests: [
        {
          title: "TC-009: 页面显示储值账户列表表格",
          status: "passed",
          duration: 2100,
          errors: [],
          steps: [
            { title: "检查表格元素", status: "passed", duration: 1200 },
            { title: "验证数据加载", status: "passed", duration: 900 },
          ],
        },
        {
          title: "TC-010: 页面包含搜索/筛选区域",
          status: "passed",
          duration: 1500,
          errors: [],
          steps: [
            { title: "检查搜索框", status: "passed", duration: 800 },
            { title: "检查筛选条件", status: "passed", duration: 700 },
          ],
        },
        {
          title: "TC-011: 页面包含创建储值充值按钮",
          status: "passed",
          duration: 1200,
          errors: [],
          steps: [
            { title: "查找创建按钮", status: "passed", duration: 600 },
            { title: "验证按钮可用", status: "passed", duration: 600 },
          ],
        },
        {
          title: "TC-012: 页面包含分页组件",
          status: "skipped",
          duration: 800,
          errors: [],
          steps: [
            { title: "查找分页组件", status: "skipped", duration: 800 },
          ],
        },
      ],
    },
    {
      title: "阶段3: 创建储值充值 - 手动输入",
      totalDuration: 15800,
      tests: [
        {
          title: "TC-013: 点击创建按钮，弹出储值充值表单",
          status: "passed",
          duration: 3200,
          errors: [],
          steps: [
            { title: "点击创建按钮", status: "passed", duration: 800 },
            { title: "等待弹窗打开", status: "passed", duration: 1500 },
            { title: "验证表单元素", status: "passed", duration: 900 },
          ],
        },
        {
          title: "TC-014: 变动类型默认选中企业储值充值",
          status: "passed",
          duration: 1100,
          errors: [],
          steps: [
            { title: "检查变动类型默认值", status: "passed", duration: 600 },
            { title: "验证选项", status: "passed", duration: 500 },
          ],
        },
        {
          title: "TC-015: 变更方式默认选中手动输入",
          status: "passed",
          duration: 1000,
          errors: [],
          steps: [
            { title: "检查变更方式默认值", status: "passed", duration: 500 },
            { title: "验证选项", status: "passed", duration: 500 },
          ],
        },
      ],
    },
    {
      title: "阶段4: 表单验证",
      totalDuration: 18500,
      tests: [
        {
          title: "TC-016: 空表单提交触发验证提示",
          status: "failed",
          duration: 2500,
          errors: ["未检测到验证提示弹窗，可能是HTML5表单验证"],
          steps: [
            { title: "清空表单", status: "passed", duration: 500 },
            { title: "点击确认", status: "passed", duration: 800 },
            { title: "检查验证提示", status: "failed", duration: 1200 },
          ],
        },
        {
          title: "TC-017: 无效手机号格式触发验证提示",
          status: "passed",
          duration: 1800,
          errors: [],
          steps: [
            { title: "输入无效手机号", status: "passed", duration: 600 },
            { title: "点击确认", status: "passed", duration: 600 },
            { title: "验证错误提示", status: "passed", duration: 600 },
          ],
        },
        {
          title: "TC-018: 金额为0触发验证提示",
          status: "failed",
          duration: 2200,
          errors: ["金额输入框允许输入0，未触发验证"],
          steps: [
            { title: "输入金额0", status: "passed", duration: 500 },
            { title: "点击确认", status: "passed", duration: 700 },
            { title: "检查验证提示", status: "failed", duration: 1000 },
          ],
        },
      ],
    },
    {
      title: "阶段5-7: 切换测试与储值流水",
      totalDuration: 6200,
      tests: [
        {
          title: "TC-019: 变动类型切换验证",
          status: "passed",
          duration: 2100,
          errors: [],
          steps: [
            { title: "切换到退款类型", status: "passed", duration: 800 },
            { title: "验证表单变化", status: "passed", duration: 700 },
            { title: "切换回充值类型", status: "passed", duration: 600 },
          ],
        },
        {
          title: "TC-020: 变更方式切换验证",
          status: "passed",
          duration: 2400,
          errors: [],
          steps: [
            { title: "切换到批量导入", status: "passed", duration: 900 },
            { title: "验证上传组件", status: "passed", duration: 700 },
            { title: "切换回手动输入", status: "passed", duration: 800 },
          ],
        },
        {
          title: "TC-021: 储值流水页面检查",
          status: "passed",
          duration: 1700,
          errors: [],
          steps: [
            { title: "导航到流水页面", status: "passed", duration: 800 },
            { title: "验证流水记录", status: "passed", duration: 900 },
          ],
        },
      ],
    },
  ],
};

// ==============================================================================
// 演示组件
// ==============================================================================

interface MockReportDemoProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function MockReportDemo({ open, onOpenChange }: MockReportDemoProps) {
  return (
    <VisualReportDialog
      open={open}
      onOpenChange={onOpenChange}
      reportData={mockXYSJGReport}
      jobName="小杨生煎-B端储值充值 自动化测试"
    />
  );
}

export default MockReportDemo;
