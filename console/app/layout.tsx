import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { WorkspaceProvider } from "./workspace-context";
import { NavBar } from "./nav-bar";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Palimpsest — Forensic Console",
  description: "Memory integrity layer for AI agents — decision timeline, belief store, and rewind.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-bg text-text">
        <WorkspaceProvider>
          <NavBar />
          <main className="flex-1 mx-auto w-full max-w-6xl px-6 py-6">{children}</main>
        </WorkspaceProvider>
      </body>
    </html>
  );
}
