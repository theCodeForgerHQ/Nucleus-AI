import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const LLM_ENGINE_URL =
  process.env.NEXT_PUBLIC_LLM_ENGINE_URL || "http://localhost:8200";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();

    const backendRes = await fetch(`${LLM_ENGINE_URL}/query/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (!backendRes.ok) {
      const data = await backendRes.json().catch(() => ({}));
      return NextResponse.json(
        { error: data.detail || data.message || backendRes.statusText },
        { status: backendRes.status },
      );
    }

    return new Response(backendRes.body, {
      status: 200,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
      },
    });
  } catch (e) {
    const message = e instanceof Error ? e.message : "Proxy request failed";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
