import { PropsWithChildren } from "react";

export default function Layout({ children }: PropsWithChildren) {
  const isLocal =
    process.env.NEXT_PUBLIC_API_URL?.startsWith("http://localhost");

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <div className="max-w-4xl mx-auto px-4 py-8 md:py-12">
        <header className="mb-10">
          <h1 className="text-2xl md:text-3xl font-semibold">Persona Demo</h1>
          <p className="text-zinc-400 text-sm md:text-base">
            Ask the Persona. Keep it natural; add filters if useful.
          </p>
        </header>

        <main>{children}</main>

      </div>
    </div>
  );
}
