import { BookOpen, GraduationCap, Inbox, LayoutDashboard, ListChecks, Megaphone, MessageCircle, Plug, SlidersHorizontal, Users } from "lucide-react";

export type NavItem = { to: string; label: string; icon: typeof Inbox; end?: boolean; admin?: boolean; hint: string };
export type NavGroup = { group: string; items: NavItem[] };

/** Every page of the assistant, grouped the way people think about the work: talk to guests, teach the assistant, set it up.
 *  There is no navigation bar: components/Launcher.tsx draws these as the buttons on the home page and in the round Menu
 *  panel on every other page. A host application may also list the same paths in its own bar (relative to VITE_BASE_PATH). */
export const NAV: NavGroup[] = [
  { group: "Guests", items: [
    { to: "/", label: "Overview", icon: LayoutDashboard, end: true, hint: "How the assistant is doing today" },
    { to: "/inbox", label: "Inbox", icon: Inbox, hint: "Messenger, Instagram and WhatsApp messages of every Page and number" },
    { to: "/comments", label: "Post comments", icon: MessageCircle, hint: "Comments on your Facebook and Instagram posts" },
    { to: "/posts", label: "Social media", icon: Megaphone, hint: "Calendar, posts, insights, audience, ads and WhatsApp broadcasts" },
    { to: "/leads", label: "Leads", icon: ListChecks, hint: "Guests interested in a program" },
    { to: "/profiles", label: "Guest profiles", icon: Users, hint: "Everything known about each guest" },
  ] },
  { group: "Assistant", items: [
    { to: "/train", label: "Train the assistant", icon: GraduationCap, hint: "Test chat, teach, find mistakes, test set" },
    { to: "/knowledge", label: "Knowledge", icon: BookOpen, hint: "Documents, Q&A, values, programs and data sheets" },
  ] },
  { group: "Settings", items: [
    { to: "/channels", label: "Pages and numbers", icon: Plug, admin: true, hint: "Connect Facebook Pages, Instagram, WhatsApp" },
    { to: "/ai", label: "AI settings", icon: SlidersHorizontal, admin: true, hint: "Models, messages, behaviour" },
  ] },
];
