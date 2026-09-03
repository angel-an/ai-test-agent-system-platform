"""Publish the advertisement API workflow into the API test artifact model."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.tools.api.artifacts_tools import (
    save_test_cases,
    save_test_plan,
    save_test_script,
)


ENDPOINT_ID = "75be961e-d687-4ba3-941d-0b9ee392c639"
PROJECT_IDENTIFIER = "PR-2"


PLAN = """# Advertisement creation API test plan

## Scope

Validate the advertisement creation contract used by the PR-2 UAT workflow.

## Endpoint

- Method: POST
- Path: /api/imarketing/v1/advertisement/add
- Related verification: GET /api/imarketing/v1/advertisement/list

## Acceptance

The request succeeds with the required headers and body, and the created name
is present in the list response. Material OSS URL extraction is best effort;
the actual value or an empty value must be recorded, and an empty value does
not fail this contract test.
"""


CASES = [
    {
        "name": "Obtain access token and session cookie",
        "description": "Use the authenticated UAT browser session as the API credential source.",
        "steps": ["Read accessToken from the authenticated URL", "Read imarketingAuth cookies"],
        "expected_result": "Both credential values are recorded without exposing secrets.",
        "priority": "high",
    },
    {
        "name": "Record material OSS URL (best effort)",
        "description": "Inspect the uploaded material preview and record the actual OSS URL or empty value.",
        "steps": ["Inspect the material preview src", "Log success or empty-value reason"],
        "expected_result": "Actual value or empty value is recorded; empty does not fail the API test.",
        "priority": "medium",
    },
    {
        "name": "Build advertisement request body",
        "description": "Build the material, setting, channel, position, and effective-time fields.",
        "steps": ["Set a unique advertisement name", "Set material and shop range fields"],
        "expected_result": "The serialized JSON body is accepted by the add endpoint.",
        "priority": "high",
    },
    {
        "name": "Create advertisement",
        "description": "POST the request to the advertisement add endpoint.",
        "steps": ["Send POST /api/imarketing/v1/advertisement/add", "Record status and response id"],
        "expected_result": "HTTP 200 and resultCode 0; an advertisement id is returned.",
        "priority": "high",
    },
    {
        "name": "Verify advertisement in list",
        "description": "Query the advertisement list and find the unique created name.",
        "steps": ["GET /api/imarketing/v1/advertisement/list", "Match the created name"],
        "expected_result": "The newly created advertisement is present in the list.",
        "priority": "high",
    },
]


SCRIPT = r'''import { test, expect } from "@playwright/test";

const baseUrl = process.env.ADVERT_API_BASE_URL || "https://imarketing-uat.xysjg.com/yundt-saas-gateway";
const token = process.env.ADVERT_ACCESS_TOKEN || "";
const cookie = process.env.ADVERT_COOKIE || "";
const name = `API_ADVERT_${Date.now()}`;

test("create advertisement and verify list", async ({ request }) => {
  const headers = {
    "access-token": token,
    cookie,
    origin: "https://imarketing-uat.xysjg.com",
    referer: "https://imarketing-uat.xysjg.com/front/saas-imarketing-web-pc/",
  };
  const body = {
    name,
    material: [{
      key: `${Date.now()}1`,
      popupData: {
        componentKey: 1,
        activityKey: "1",
        appId: "wx37b9ba33202ba852",
        itemName: "点单小程序",
        idx: 2,
        imageUrl: process.env.ADVERT_MATERIAL_OSS_URL || "",
        funLink: { code: "19", path: "/pages/store/goods", name: "点餐", type: "19" },
      },
      materialTimeType: 0,
      materialTimeRanges: [],
      setting: [{
        key: `${Date.now()}2`,
        areaRange: { areaRangeType: "", areaCodes: [] },
        position: 4,
        type: "POPUP",
        rangeType: "TERM",
        shopRange: { applyShopType: "0", shopCodes: ["SH8888"] },
        showRangeType: "1",
      }],
      weight: 999,
      businessType: 0,
      pushChannel: "1,2",
      timeType: 0,
    }],
    timeType: 0,
    effectiveStartTime: new Date().toISOString().slice(0, 19).replace("T", " "),
    effectiveEndTime: null,
    week: null,
  };

  const created = await request.post(`${baseUrl}/api/imarketing/v1/advertisement/add`, { headers, data: body });
  expect(created.ok()).toBeTruthy();
  expect((await created.json()).resultCode).toBe("0");

  const listed = await request.get(`${baseUrl}/api/imarketing/v1/advertisement/list?pageNum=1&pageSize=10`, { headers });
  expect(listed.ok()).toBeTruthy();
  const payload = await listed.json();
  const records = Array.isArray(payload.data) ? payload.data : (payload.data?.records || payload.data?.list || []);
  expect(records.some((record: { name?: string }) => record.name === name)).toBeTruthy();
});
'''


async def main() -> None:
    results = {
        "plan": await save_test_plan.ainvoke({
            "endpoint_id": ENDPOINT_ID,
            "plan_content": PLAN,
            "plan_format": "markdown",
            "project_identifier": PROJECT_IDENTIFIER,
        }),
        "cases": await save_test_cases.ainvoke({
            "endpoint_id": ENDPOINT_ID,
            "test_cases": CASES,
            "project_identifier": PROJECT_IDENTIFIER,
        }),
        "script": await save_test_script.ainvoke({
            "endpoint_id": ENDPOINT_ID,
            "script_content": SCRIPT,
            "script_language": "typescript",
            "script_format": "playwright",
            "project_identifier": PROJECT_IDENTIFIER,
        }),
    }
    for key, value in results.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    asyncio.run(main())
