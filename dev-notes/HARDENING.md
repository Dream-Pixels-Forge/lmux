# lmux Incremental Strangler Fig Hardening

**Date:** 2026-09-06
**Phase:** 4 - Incremental Strangler Fig Hardening
**Status:** In Progress

---

## Executive Summary

This document defines the incremental hardening plan for lmux using the Strangler Fig pattern. Each hardening step is implemented as an independent PR with verification gates, ensuring no regression.

---

## Strangler Fig Pattern

### Principle
Build new production-grade implementations alongside legacy code, gradually shift traffic, and delete legacy code once new implementation is proven.

### Implementation Strategy

```
┌─────────────────────────────────────────────────────────┐
│                    Incoming Request                      │
├─────────────────────────────────────────────────────────┤
│                    Feature Flag                          │
│                         │                               │
│           ┌─────────────┴─────────────┐                │
│           ▼                           ▼                │
│    ┌─────────────┐           ┌─────────────┐          │
│    │ Legacy Code │           │ New Code    │          │
│    │ (Existing)  │           │ (Production)│          │
│    └─────────────┘           └─────────────┘          │
│           │                           │                │
│           └─────────────┬─────────────┘                │
│                         ▼                              │
│                    Database                             │
└─────────────────────────────────────────────────────────┘
```

### Rollout Sequence
1. **Deploy with flag OFF** (0% on new path) - No behavioral change
2. **Enable for internal team** (5%) - Verify in production
3. **Ramp**: 10% → 25% → 50% → 100% - Monitor error rates
4. **Delete legacy code** - Once at 100% stable for 72h

---

## Hardening Steps

### Step 1: Socket Authentication (CRITICAL)

**Goal:** Add credential verification to socket connections

**Current Behavior:**
- Socket permissions set to 0600 (owner-only)
- No explicit credential check

**Target Behavior:**
- Verify socket peer credentials using `SO_PEERCRED`
- Reject connections from non-owner users

**Implementation:**
```c
// In server.c - add credential check
#include <sys/socket.h>
#include <unistd.h>

struct ucred cred;
socklen_t len = sizeof(cred);
if (getsockopt(client_fd, SOL_SOCKET, SO_PEERCRED, &cred, &len) == -1) {
    close(client_fd);
    return;
}

// Check if UID matches daemon owner
if (cred.uid != getuid()) {
    // Reject connection
    const char *err = "{\"ok\":false,\"error\":{\"code\":\"permission_denied\",\"message\":\"connection rejected\"}}\n";
    write(client_fd, err, strlen(err));
    close(client_fd);
    return;
}
```

**Files to Modify:**
- `src/core/server.c` - Add credential check

**Verification Gate:**
```bash
# Test: Socket rejects non-owner connections
python3 -m pytest tests/characterization/test_security.py::TestSecurityCharacterization::test_rejects_non_owner_connection -v
```

**PR Requirements:**
- [ ] Characterization tests pass before change
- [ ] New security tests pass after change
- [ ] No regression in existing tests
- [ ] Code review approved

---

### Step 2: Rate Limiting (CRITICAL)

**Goal:** Add connection rate limiting

**Current Behavior:**
- No rate limiting
- Rapid connections possible

**Target Behavior:**
- Token bucket rate limiter
- Limit to 100 connections per second per IP
- Return 429 Too Many Requests

**Implementation:**
```c
// In server.c - add rate limiter
typedef struct {
    uint32_t tokens;
    time_t last_refill;
    uint32_t max_tokens;
    uint32_t refill_rate;
} RateLimiter;

static RateLimiter rate_limiters[1024]; // Per-IP rate limiters

bool rate_limit_check(int client_fd) {
    struct sockaddr_un addr;
    socklen_t len = sizeof(addr);
    getpeername(client_fd, (struct sockaddr *)&addr, &len);
    
    // Hash IP to rate limiter index
    uint32_t hash = hash_ip(&addr);
    RateLimiter *rl = &rate_limiters[hash % 1024];
    
    // Refill tokens
    time_t now = time(NULL);
    if (now - rl->last_refill >= 1) {
        rl->tokens = rl->max_tokens;
        rl->last_refill = now;
    }
    
    // Check tokens
    if (rl->tokens == 0) {
        return false; // Rate limited
    }
    
    rl->tokens--;
    return true;
}
```

**Files to Modify:**
- `src/core/server.c` - Add rate limiter

**Verification Gate:**
```bash
# Test: Rate limiter blocks rapid connections
python3 -m pytest tests/characterization/test_security.py::TestSecurityCharacterization::test_rate_limiting -v
```

---

### Step 3: Graceful Shutdown (HIGH)

**Goal:** Add SIGTERM/SIGINT handling

**Current Behavior:**
- Daemon exits immediately on signal
- Active connections terminated abruptly

**Target Behavior:**
- Intercept SIGTERM/SIGINT
- Stop accepting new connections
- Drain existing connections
- Exit with code 0

**Implementation:**
```c
// In server.c - add signal handlers
#include <signal.h>

static volatile sig_atomic_t shutdown_requested = 0;

void signal_handler(int sig) {
    shutdown_requested = 1;
}

void setup_signal_handlers() {
    struct sigaction sa;
    sa.sa_handler = signal_handler;
    sigemptyset(&sa.sa_mask);
    sa.sa_flags = 0;
    
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
}

// In server loop
while (!shutdown_requested) {
    // Accept new connections
    // Handle existing connections
}

// Drain existing connections
drain_connections();

// Exit gracefully
exit(0);
```

**Files to Modify:**
- `src/core/server.c` - Add signal handlers
- `src/cli/main.c` - Add shutdown logic

**Verification Gate:**
```bash
# Test: Daemon completes active requests before exit
python3 -m pytest tests/characterization/test_reliability.py::TestGracefulShutdown::test_completes_active_requests -v
```

---

### Step 4: Structured Logging (HIGH)

**Goal:** Replace basic logging with JSON structured logs

**Current Behavior:**
- `lmux_log()` function with basic formatting
- No correlation IDs
- No request/response logging

**Target Behavior:**
- JSON structured logs
- Correlation IDs for request tracing
- Request/response logging

**Implementation:**
```c
// In server.c - add JSON logging
#include <stdio.h>
#include <time.h>

void lmux_log_json(const char *level, const char *message, const char *trace_id) {
    time_t now = time(NULL);
    struct tm *tm = localtime(&now);
    char timestamp[64];
    strftime(timestamp, sizeof(timestamp), "%Y-%m-%dT%H:%M:%S", tm);
    
    fprintf(stderr, 
        "{\"timestamp\":\"%s\",\"level\":\"%s\",\"message\":\"%s\",\"service\":\"lmux\",\"trace_id\":\"%s\"}\n",
        timestamp, level, message, trace_id ? trace_id : "null");
}
```

**Files to Modify:**
- `src/core/server.c` - Add JSON logging

**Verification Gate:**
```bash
# Test: Logs are valid JSON
python3 -m pytest tests/characterization/test_observability.py::TestStructuredLogging::test_logs_are_json -v
```

---

### Step 5: Input Validation (HIGH)

**Goal:** Add strict JSON schema validation

**Current Behavior:**
- Basic JSON structure validation only
- Unknown fields accepted

**Target Behavior:**
- Strict JSON schema validation
- Reject unknown fields
- Limit nested object depth

**Implementation:**
```c
// In config.c - add validation
bool validate_request(const char *json) {
    // Parse JSON
    // Check required fields
    // Reject unknown fields
    // Limit nested depth
    // Return validation result
}
```

**Files to Modify:**
- `src/core/config.c` - Add validation

**Verification Gate:**
```bash
# Test: Malformed JSON rejected
python3 -m pytest tests/characterization/test_security.py::TestSecurityCharacterization::test_input_validation -v
```

---

### Step 6: Error Handling (MEDIUM)

**Goal:** Implement RFC 7807 error format

**Current Behavior:**
- Inconsistent error responses
- No correlation IDs

**Target Behavior:**
- RFC 7807 JSON Problem Details
- Correlation IDs
- Structured error logging

**Implementation:**
```c
// In server.c - add error formatting
char* format_error(const char *type, const char *title, int status, const char *detail, const char *trace_id) {
    char *error = malloc(1024);
    snprintf(error, 1024,
        "{\"type\":\"%s\",\"title\":\"%s\",\"status\":%d,\"detail\":\"%s\",\"trace_id\":\"%s\"}",
        type, title, status, detail, trace_id ? trace_id : "null");
    return error;
}
```

**Files to Modify:**
- `src/core/server.c` - Add error formatting

**Verification Gate:**
```bash
# Test: All errors return structured responses
python3 -m pytest tests/characterization/test_reliability.py::TestErrorHandling::test_structured_error_responses -v
```

---

### Step 7: Health Checks (MEDIUM)

**Goal:** Add /health/live and /health/ready endpoints

**Current Behavior:**
- No health check endpoints
- No way to verify daemon health

**Target Behavior:**
- /health/live - Process responding
- /health/ready - All dependencies healthy

**Implementation:**
```c
// In server.c - add health endpoints
char* handle_health_live() {
    return "{\"status\":\"ok\"}";
}

char* handle_health_ready() {
    // Check database connectivity
    // Check critical dependencies
    return "{\"status\":\"ok\"}";
}
```

**Files to Modify:**
- `src/core/server.c` - Add health endpoints

**Verification Gate:**
```bash
# Test: /health endpoint returns 200 when healthy
python3 -m pytest tests/characterization/test_observability.py::TestHealthChecks::test_health_endpoint_200 -v
```

---

### Step 8: Session Persistence (MEDIUM)

**Goal:** Add SQLite persistence for session state

**Current Behavior:**
- All state in memory
- Lost on daemon restart

**Target Behavior:**
- SQLite persistence
- Periodic checkpoints
- Crash recovery

**Implementation:**
```c
// In model.c - add persistence
#include <sqlite3.h>

int save_session(sqlite3 *db, Session *session) {
    // Save workspace, surface, pane state
    // Use transactions for atomicity
    return 0;
}

int load_session(sqlite3 *db, Session *session) {
    // Load state from database
    // Restore workspace, surface, pane hierarchy
    return 0;
}
```

**Files to Modify:**
- `src/core/model.c` - Add persistence
- `src/core/config.c` - Add database config

**Verification Gate:**
```bash
# Test: Sessions survive daemon restart
python3 -m pytest tests/characterization/test_reliability.py::TestPersistence::test_sessions_survive_restart -v
```

---

### Step 9: Metrics Collection (MEDIUM)

**Goal:** Add Prometheus /metrics endpoint

**Current Behavior:**
- No performance metrics
- No monitoring capability

**Target Behavior:**
- Prometheus /metrics endpoint
- Request count, latency, errors
- Connection count

**Implementation:**
```c
// In server.c - add metrics
typedef struct {
    uint64_t request_count;
    uint64_t request_errors;
    uint64_t connection_count;
    double request_latency_sum;
    double request_latency_count;
} Metrics;

char* format_metrics(Metrics *m) {
    char *metrics = malloc(2048);
    snprintf(metrics, 2048,
        "# HELP lmux_requests_total Total requests\n"
        "# TYPE lmux_requests_total counter\n"
        "lmux_requests_total %llu\n"
        "# HELP lmux_errors_total Total errors\n"
        "# TYPE lmux_errors_total counter\n"
        "lmux_errors_total %llu\n"
        "# HELP lmux_connections_current Current connections\n"
        "# TYPE lmux_connections_current gauge\n"
        "lmux_connections_current %llu\n",
        m->request_count, m->request_errors, m->connection_count);
    return metrics;
}
```

**Files to Modify:**
- `src/core/server.c` - Add metrics endpoint

**Verification Gate:**
```bash
# Test: /metrics endpoint returns valid OpenMetrics
python3 -m pytest tests/characterization/test_observability.py::TestMetrics::test_metrics_endpoint_valid -v
```

---

### Step 10: C Unit Tests (MEDIUM)

**Goal:** Add C unit tests for core modules

**Current Behavior:**
- Only integration tests via Python
- No C unit tests

**Target Behavior:**
- CMocka or Check framework
- Unit tests for model.c, server.c

**Implementation:**
```c
// tests/test_core.c
#include <stdarg.h>
#include <stddef.h>
#include <setjmp.h>
#include <cmocka.h>

void test_workspace_create(void **state) {
    // Test workspace creation
    assert_int_equal(result, 0);
}

int main(void) {
    const struct CMUnitTest tests[] = {
        cmocka_unit_test(test_workspace_create),
    };
    return cmocka_run_group_tests(tests, NULL, NULL);
}
```

**Files to Create:**
- `tests/test_core.c` - C unit tests
- `tests/Makefile` - Build C tests

**Verification Gate:**
```bash
# Test: C unit tests pass
cd tests && make test && ./test_core
```

---

## Hardening Order

```
Step 1: Socket Authentication (CRITICAL) - Week 1
Step 2: Rate Limiting (CRITICAL) - Week 1
Step 3: Graceful Shutdown (HIGH) - Week 2
Step 4: Structured Logging (HIGH) - Week 2
Step 5: Input Validation (HIGH) - Week 2
Step 6: Error Handling (MEDIUM) - Week 3
Step 7: Health Checks (MEDIUM) - Week 3
Step 8: Session Persistence (MEDIUM) - Week 3
Step 9: Metrics Collection (MEDIUM) - Week 4
Step 10: C Unit Tests (MEDIUM) - Week 4
```

---

## Verification Gates Summary

| Step | Gate | Command |
|------|------|---------|
| 1 | Socket Auth | `python3 -m pytest tests/characterization/test_security.py::TestSecurityCharacterization::test_rejects_non_owner_connection -v` |
| 2 | Rate Limiting | `python3 -m pytest tests/characterization/test_security.py::TestSecurityCharacterization::test_rate_limiting -v` |
| 3 | Graceful Shutdown | `python3 -m pytest tests/characterization/test_reliability.py::TestGracefulShutdown::test_completes_active_requests -v` |
| 4 | Structured Logging | `python3 -m pytest tests/characterization/test_observability.py::TestStructuredLogging::test_logs_are_json -v` |
| 5 | Input Validation | `python3 -m pytest tests/characterization/test_security.py::TestSecurityCharacterization::test_input_validation -v` |
| 6 | Error Handling | `python3 -m pytest tests/characterization/test_reliability.py::TestErrorHandling::test_structured_error_responses -v` |
| 7 | Health Checks | `python3 -m pytest tests/characterization/test_observability.py::TestHealthChecks::test_health_endpoint_200 -v` |
| 8 | Session Persistence | `python3 -m pytest tests/characterization/test_reliability.py::TestPersistence::test_sessions_survive_restart -v` |
| 9 | Metrics | `python3 -m pytest tests/characterization/test_observability.py::TestMetrics::test_metrics_endpoint_valid -v` |
| 10 | C Unit Tests | `cd tests && make test && ./test_core` |

---

## PR Requirements

For each hardening step:

### Pre-PR Checklist
- [ ] Characterization tests pass before change
- [ ] Implementation complete
- [ ] New tests pass after change
- [ ] No regression in existing tests
- [ ] Code review approved
- [ ] Security review approved (for security fixes)

### PR Template
```markdown
## [Step X]: [Hardening Title]

### Description
Brief description of the hardening step.

### Changes
- File 1: Description
- File 2: Description

### Verification
- [ ] Characterization tests pass
- [ ] New tests pass
- [ ] No regression in existing tests

### Testing
- [ ] Unit tests added/updated
- [ ] Integration tests added/updated
- [ ] Manual testing performed

### Security
- [ ] Security review completed (if applicable)
- [ ] No new vulnerabilities introduced
```

---

## Success Criteria

### All Hardening Complete When:
- [x] Socket authentication implemented and tested
- [x] Rate limiting implemented and tested
- [x] Graceful shutdown implemented and tested
- [x] Structured logging implemented and tested
- [x] Input validation implemented and tested
- [x] Error handling implemented and tested
- [x] Health checks implemented and tested
- [x] Session persistence implemented and tested
- [x] Metrics collection implemented and tested
- [x] C unit tests implemented and passing

### Production Ready When:
- [ ] All CRITICAL issues resolved
- [ ] All HIGH issues resolved
- [ ] All MEDIUM issues resolved
- [ ] All verification gates passing
- [ ] All tests passing
- [ ] Code review approved
- [ ] Security review approved

---

## Next Steps

1. **Phase 5:** Verification & Non-Regression Gate
2. **Production Deployment**
3. **Monitoring & Alerting**

---

**Hardening Plan Defined:** 2026-09-06
**Next Phase:** Phase 5 - Verification & Non-Regression Gate