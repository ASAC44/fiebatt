import Link from "next/link";
import Image from "next/image";

import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";

export default function Home() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-background text-foreground">
      <div className="flex items-center gap-3">
        <Button asChild>
          <Link href="/projects">
            <Image
              alt=""
              aria-hidden
              className="size-4"
              height={16}
              src="/logo.png"
              width={16}
            />
            Projects
          </Link>
        </Button>
        <ThemeToggle />
      </div>
    </main>
  );
}
