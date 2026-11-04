import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { Navigate, Outlet, useLocation, useNavigate, useParams } from "react-router-dom";

import { ApiError, onUnauthenticated } from "../../api/client";
import { useAcceptInvite, useInvite, useLogin, useMe } from "../../api/hooks";
import { Loading } from "../../components/ProblemNotice";
import { AUTH_ERROR, T } from "../../strings/ko";

function errorText(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof ApiError) return AUTH_ERROR[error.code] ?? error.message;
  return "처리하지 못했습니다";
}

/** 로그인이 필요한 화면을 감싼다. 로그인이 필요하다는 응답(401)만 로그인 화면으로 보내고, 다른 오류는 각 화면이 보여 준다. */
export function RequireAuth() {
  const me = useMe();
  const client = useQueryClient();
  const location = useLocation();

  useEffect(() => {
    onUnauthenticated(() => void client.invalidateQueries({ queryKey: ["me"] }));
    return () => onUnauthenticated(null);
  }, [client]);

  if (me.isPending) return <Loading />;
  if (me.error instanceof ApiError && me.error.status === 401) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }
  return <Outlet />;
}

export function LoginPage() {
  const me = useMe();
  const navigate = useNavigate();
  const location = useLocation();
  const login = useLogin();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  function submit(e: FormEvent) {
    e.preventDefault();
    login.mutate(
      { email: email.trim(), password },
      { onSuccess: () => void navigate((location.state as { from?: string } | null)?.from ?? "/", { replace: true }) },
    );
  }

  if (me.data?.auth_mode === "dev") return <Navigate to="/" replace />;
  return (
    <>
      <h1>로그인</h1>
      <form className="card" onSubmit={submit}>
        <p>
          <label>
            이메일 <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </label>
        </p>
        <p>
          <label>
            비밀번호{" "}
            <input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
        </p>
        <p>
          <button type="submit" disabled={login.isPending}>로그인</button>
        </p>
        {errorText(login.error) ? (
          <p className="notice notice--error" role="alert">{errorText(login.error)}</p>
        ) : null}
      </form>
      <p className="muted">계정은 초대를 받은 사람만 만들 수 있습니다.</p>
    </>
  );
}

export function InvitePage() {
  const { token = "" } = useParams();
  const navigate = useNavigate();
  const invite = useInvite(token);
  const accept = useAcceptInvite(token);
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [consent, setConsent] = useState(false);

  if (invite.isPending) return <Loading />;
  if (invite.error || !invite.data) {
    return (
      <>
        <h1>초대 확인</h1>
        <p className="notice notice--error" role="alert">유효하지 않거나 만료된 초대입니다. 초대한 사람에게 새 초대를 요청해 주세요.</p>
      </>
    );
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    accept.mutate({ display_name: name.trim(), password, consent }, { onSuccess: () => void navigate("/login", { replace: true }) });
  }

  return (
    <>
      <h1>계정 만들기</h1>
      <p className="muted">{invite.data.email} 주소로 초대되었습니다.</p>
      <form className="card" onSubmit={submit}>
        <p>
          <label>
            이름 <input required maxLength={80} autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />
          </label>
        </p>
        <p>
          <label>
            비밀번호(10자 이상){" "}
            <input type="password" required minLength={10} maxLength={128} autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
        </p>
        <p>
          <label>
            <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} /> {T.consentLabel}
          </label>
        </p>
        <p>
          <button type="submit" disabled={!consent || accept.isPending}>계정 만들기</button>
        </p>
        {errorText(accept.error) ? <p className="notice notice--error" role="alert">{errorText(accept.error)}</p> : null}
      </form>
    </>
  );
}
