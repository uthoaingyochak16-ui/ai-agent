import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import { Spinner } from "./components/ui";
import { useAuth } from "./lib/auth";
import Login from "./pages/Login";

const Dashboard = lazy(() => import("./pages/Dashboard"));
const InboxPage = lazy(() => import("./pages/Inbox"));
const Profiles = lazy(() => import("./pages/Profiles"));
const ProfileDetail = lazy(() => import("./pages/ProfileDetail"));
const Knowledge = lazy(() => import("./pages/Knowledge"));
const QAManager = lazy(() => import("./pages/QAManager"));
const Channels = lazy(() => import("./pages/Channels"));
const AISettings = lazy(() => import("./pages/AISettings"));
const TestChat = lazy(() => import("./pages/TestChat"));

function Protected({ admin }: { admin?: boolean }) {
  const { user, loading } = useAuth();
  if (loading) return <Spinner label="Checking session" />;
  if (!user) return <Navigate to="/login" replace />;
  if (admin && user.role !== "admin") return <Navigate to="/" replace />;
  return <Layout />;
}

export default function App() {
  return (
    <Suspense fallback={<Spinner />}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<Protected />}>
          <Route index element={<Dashboard />} />
          <Route path="inbox" element={<InboxPage />} />
          <Route path="profiles" element={<Profiles />} />
          <Route path="profiles/:id" element={<ProfileDetail />} />
          <Route path="knowledge" element={<Knowledge />} />
          <Route path="qa" element={<QAManager />} />
          <Route path="test-chat" element={<TestChat />} />
        </Route>
        <Route element={<Protected admin />}>
          <Route path="channels" element={<Channels />} />
          <Route path="ai" element={<AISettings />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  );
}
