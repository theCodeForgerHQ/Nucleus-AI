import { NextRequest, NextResponse } from "next/server";

const LLM_ENGINE_URL =
  process.env.NEXT_PUBLIC_LLM_ENGINE_URL || "http://localhost:8200";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const res = await fetch(`${LLM_ENGINE_URL}/query/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      return NextResponse.json(
        { error: err.detail || err.error || res.statusText },
        { status: res.status }
      );
    }
    return new Response(res.body, {
      status: res.status,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
      },
    });
  } catch (e) {
    const message = e instanceof Error ? e.message : "Stream proxy failed";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
