# lmux Verification & Non-Regression Gate

**Date:** 2026-09-06
**Phase:** 5 - Verification & Non-Regression Gate
**Status:** In Progress

---

## Executive Summary

This document defines the verification gates for lmux production readiness. Every hardening step must pass these gates before being considered complete.

---

## Verification Gate Structure

### Gate Levels

#### Level 1: Unit Tests
- **Scope:** Individual functions/modules
- **Speed:** Fast (< 1 second)
- **Frequency:** Every commit

#### Level 2: Integration Tests
- **Scope:** Module interactions
- **Speed:** Medium (1-10 seconds)
- **Frequency:** Every PR

#### Level 3: Characterization Tests
- **Scope:** System behavior documentation
- **Speed:** Medium (1-10 seconds)
- **Frequency:** Every hardening step

#### Level 4: Security Tests
- **Scope:** Security controls
- **Speed:** Slow (10-60 seconds)
- **Frequency:** Every security fix

#### Level 5: Performance Tests
- **Scope:** Latency, throughput
- **Speed:** Slow (60+ seconds)
- **Frequency:** Before release

---

## Verification Gates

### Gate 1: Code Quality

#### Static Analysis
```bash
# C code: clang-tidy
clang-tidy src/core/*.c -- -Iinclude

# Python code: pylint
pylint gui/*.py

# Both: format check
clang-format --dry-run src/core/*.c
black --check gui/*.py
```

#### Compilation Check
```bash
# Build core library
node scripts/build-core.mjs

# Build CLI binary
node scripts/build-cli.mjs

# Verify no warnings
make clean && make 2>&1 | grep -i warning | wc -l  # Should be 0
```

**Gate Pass Criteria:**
- [ ] Zero compilation warnings
- [ ] Zero static analysis errors
- [ ] Code formatted correctly

---

### Gate 2: Unit Tests

#### C Unit Tests
```bash
cd tests
make test
./test_core
```

#### Python Unit Tests
```bash
python3 -m pytest tests/unit/ -v
```

**Gate Pass Criteria:**
- [ ] All C unit tests pass
- [ ] All Python unit tests pass
- [ ] No test failures
- [ ] Test coverage > 80%

---

### Gate 3: Integration Tests

#### Existing Integration Tests
```bash
python3 -m pytest tests/test_integration.py -v
```

#### Characterization Tests
```bash
python3 -m pytest tests/characterization/ -v
```

**Gate Pass Criteria:**
- [ ] All integration tests pass
- [ ] All characterization tests pass
- [ ] No regressions from previous run

---

### Gate 4: Security Tests

#### Socket Authentication
```bash
python3 -m pytest tests/characterization/test_security.py::TestSecurityCharacterization::test_rejects_non_owner_connection -v
```

#### Rate Limiting
```bash
python3 -m pytest tests/characterization/test_security.py::TestSecurityCharacterization::test_rate_limiting -v
```

#### Input Validation
```bash
python3 -m pytest tests/characterization/test_security.py::TestSecurityCharacterization::test_input_validation -v
```

#### Dependency Scanning
```bash
# Scan for HIGH/CRITICAL CVEs
trivy fs --severity HIGH,CRITICAL --exit-code 1 .
```

**Gate Pass Criteria:**
- [ ] Socket authentication working
- [ ] Rate limiting working
- [ ] Input validation working
- [ ] No HIGH/CRITICAL CVEs

---

### Gate 5: Reliability Tests

#### Graceful Shutdown
```bash
python3 -m pytest tests/characterization/test_reliability.py::TestGracefulShutdown::test_completes_active_requests -v
```

#### Error Handling
```bash
python3 -m pytest tests/characterization/test_reliability.py::TestErrorHandling::test_structured_error_responses -v
```

#### Session Persistence
```bash
python3 -m pytest tests/characterization/test_reliability.py::TestPersistence::test_sessions_survive_restart -v
```

**Gate Pass Criteria:**
- [ ] Graceful shutdown working
- [ ] Error handling working
- [ ] Session persistence working

---

### Gate 6: Observability Tests

#### Structured Logging
```bash
python3 -m pytest tests/characterization/test_observability.py::TestStructuredLogging::test_logs_are_json -v
```

#### Metrics Collection
```bash
python3 -m pytest tests/characterization/test_observability.py::TestMetrics::test_metrics_endpoint_valid -v
```

#### Health Checks
```bash
python3 -m pytest tests/characterization/test_observability.py::TestHealthChecks::test_health_endpoint_200 -v
```

**Gate Pass Criteria:**
- [ ] Structured logging working
- [ ] Metrics collection working
- [ ] Health checks working

---

### Gate 7: Sanitizer Checks

#### AddressSanitizer (ASan)
```bash
ASAN_OPTIONS=detect_leaks=1 python3 -m pytest tests/ -v
```

#### UndefinedBehaviorSanitizer (UBSan)
```bash
UBSAN_OPTIONS=print_stacktrace=1 python3 -m pytest tests/ -v
```

**Gate Pass Criteria:**
- [ ] No ASan violations
- [ ] No UBSan violations
- [ ] No memory leaks

---

### Gate 8: Performance Tests

#### Latency Benchmark
```bash
python3 tests/benchmarks.py::TestPerformance::test_request_latency
```

#### Throughput Benchmark
```bash
python3 tests/benchmarks.py::TestPerformance::test_throughput
```

**Gate Pass Criteria:**
- [ ] Request latency < 10ms (p95)
- [ ] Throughput > 1000 requests/second
- [ ] No performance regressions

---

## Verification Pipeline

### CI/CD Pipeline

```yaml
# .github/workflows/verification.yml
name: Verification Pipeline

on: [push, pull_request]

jobs:
  code-quality:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Install dependencies
        run: pnpm install
      - name: Static analysis
        run: |
          clang-tidy src/core/*.c -- -Iinclude
          pylint gui/*.py
      - name: Format check
        run: |
          clang-format --dry-run src/core/*.c
          black --check gui/*.py

  unit-tests:
    runs-on: ubuntu-latest
    needs: code-quality
    steps:
      - uses: actions/checkout@v3
      - name: Install dependencies
        run: pnpm install
      - name: C unit tests
        run: |
          cd tests
          make test
          ./test_core
      - name: Python unit tests
        run: python3 -m pytest tests/unit/ -v

  integration-tests:
    runs-on: ubuntu-latest
    needs: unit-tests
    steps:
      - uses: actions/checkout@v3
      - name: Install dependencies
        run: pnpm install
      - name: Integration tests
        run: python3 -m pytest tests/test_integration.py -v
      - name: Characterization tests
        run: python3 -m pytest tests/characterization/ -v

  security-tests:
    runs-on: ubuntu-latest
    needs: integration-tests
    steps:
      - uses: actions/checkout@v3
      - name: Security tests
        run: python3 -m pytest tests/characterization/test_security.py -v
      - name: Dependency scanning
        uses: aquasecurity/trivy-action@master
        with:
          scan-type: 'fs'
          severity: 'HIGH,CRITICAL'
          exit-code: '1'

  sanitizer-checks:
    runs-on: ubuntu-latest
    needs: integration-tests
    steps:
      - uses: actions/checkout@v3
      - name: ASan check
        run: ASAN_OPTIONS=detect_leaks=1 python3 -m pytest tests/ -v
      - name: UBSan check
        run: UBSAN_OPTIONS=print_stacktrace=1 python3 -m pytest tests/ -v

  performance-tests:
    runs-on: ubuntu-latest
    needs: sanitizer-checks
    steps:
      - uses: actions/checkout@v3
      - name: Performance tests
        run: python3 tests/benchmarks.py -v
```

---

## Verification Checklist

### Before Each Commit
- [ ] Code compiles without warnings
- [ ] Code formatted correctly
- [ ] Unit tests pass

### Before Each PR
- [ ] All unit tests pass
- [ ] All integration tests pass
- [ ] All characterization tests pass
- [ ] Code review approved

### Before Each Release
- [ ] All security tests pass
- [ ] All sanitizer checks pass
- [ ] All performance tests pass
- [ ] No HIGH/CRITICAL CVEs
- [ ] Documentation updated
- [ ] Changelog updated

---

## Regression Detection

### Automated Regression Detection
```bash
# Compare test results with baseline
python3 tests/regression.py --baseline=baseline.json --current=current.json

# Detect performance regressions
python3 tests/perf_regression.py --baseline=perf_baseline.json --current=perf_current.json
```

### Manual Regression Checklist
- [ ] All existing features still work
- [ ] No new error messages
- [ ] No performance degradation
- [ ] No security regressions

---

## Rollback Procedure

### If Verification Gate Fails

1. **Identify failure point**
   ```bash
   # Check which gate failed
   python3 tests/gate_check.py --gate=all
   ```

2. **Diagnose root cause**
   ```bash
   # Run tests with verbose output
   python3 -m pytest tests/ -v --tb=long
   ```

3. **Fix the issue**
   - If code issue: fix and re-run tests
   - If test issue: fix test and verify
   - If environment issue: fix environment and re-run

4. **Re-run verification**
   ```bash
   # Re-run all gates
   python3 tests/gate_check.py --gate=all
   ```

5. **If still failing: rollback**
   ```bash
   # Revert to last known good state
   git revert HEAD
   git push
   ```

---

## Success Criteria

### Production Ready When:
- [ ] All verification gates pass
- [ ] All characterization tests pass
- [ ] All security tests pass
- [ ] All sanitizer checks pass
- [ ] All performance tests pass
- [ ] No regressions from baseline
- [ ] Code review approved
- [ ] Security review approved

### Release Ready When:
- [ ] All production ready criteria met
- [ ] Documentation complete
- [ ] Changelog complete
- [ ] Release notes complete
- [ ] SBOM generated
- [ ] Container signed (if applicable)

---

## Next Steps

1. **Implement verification gates** in CI/CD
2. **Run verification** on current codebase
3. **Fix any failures** before proceeding
4. **Production Deployment**

---

**Verification Gate Defined:** 2026-09-06
**Next Phase:** Production Deployment