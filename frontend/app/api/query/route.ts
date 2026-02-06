import { NextRequest, NextResponse } from "next/server";

const LLM_ENGINE_URL =
  process.env.NEXT_PUBLIC_LLM_ENGINE_URL || "http://localhost:8200";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const res = await fetch(`${LLM_ENGINE_URL}/query`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      return NextResponse.json(
        { error: data.detail || data.message || res.statusText },
        { status: res.status }
      );
    }
    return NextResponse.json(data);
  } catch (e) {
    const message = e instanceof Error ? e.message : "Proxy request failed";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
