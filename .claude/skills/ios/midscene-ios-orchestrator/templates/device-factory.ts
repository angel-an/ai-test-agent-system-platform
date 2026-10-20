import { IOSDevice, IOSAgent } from '@midscene/ios';
import 'dotenv/config';

const WDA_BASE_URL = process.env.WDA_BASE_URL || 'http://localhost:8100';

export function createDevice(): IOSDevice {
  return new IOSDevice({ baseUrl: WDA_BASE_URL });
}

export function createAgent(page: IOSDevice, reportName: string): IOSAgent {
  return new IOSAgent(page, {
    generateReport: true,
    reportFileName: reportName,
    aiActContext: `iOS App 自动化测试。
    如有权限弹窗点击"允许"或"好"。
    语言为中文。`,
  });
}
