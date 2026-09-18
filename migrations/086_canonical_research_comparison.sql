-- Bind the canonical comparison document to the exact persisted evidence bundle.
CREATE OR REPLACE FUNCTION guard_research_comparison_insert()
RETURNS trigger AS $$
DECLARE
    expected_control_experiment_id TEXT;
    control_experiment_id TEXT;
    variant_run_experiment_id TEXT;
    control_as_of TIMESTAMPTZ;
    variant_as_of TIMESTAMPTZ;
    stored_control_result_fingerprint CHAR(64);
    stored_variant_result_fingerprint CHAR(64);
    expected_protocol_hash CHAR(64);
    completed_stage_count INTEGER;
    expected_evaluation_results JSONB;
    expected_comparison JSONB;
BEGIN
    SELECT rep.protocol_hash INTO expected_protocol_hash
    FROM research_evaluation_plans rep
    WHERE rep.variant_experiment_id = NEW.variant_experiment_id;

    IF expected_protocol_hash IS NULL THEN
        RAISE EXCEPTION 'RESEARCH_COMPARISON_EVALUATION_PLAN_REQUIRED' USING ERRCODE='23514';
    END IF;

    SELECT count(*), jsonb_object_agg(
        rer.stage,
        jsonb_build_object(
            'result_fingerprint', rer.result_fingerprint,
            'result', rer.canonical_result
        )
    )
    INTO completed_stage_count, expected_evaluation_results
    FROM research_evaluation_results rer
    WHERE rer.variant_experiment_id=NEW.variant_experiment_id
      AND rer.control_run_id=NEW.control_run_id
      AND rer.variant_run_id=NEW.variant_run_id
      AND rer.protocol_hash=expected_protocol_hash;

    IF completed_stage_count <> 9 THEN
        RAISE EXCEPTION 'RESEARCH_COMPARISON_EVALUATION_RESULTS_INCOMPLETE' USING ERRCODE='23514';
    END IF;

    SELECT ev.control_experiment_id INTO expected_control_experiment_id
    FROM experiment_variants ev WHERE ev.variant_experiment_id=NEW.variant_experiment_id;

    SELECT rr.experiment_id, rr.as_of INTO control_experiment_id, control_as_of
    FROM research_runs rr WHERE rr.research_run_id=NEW.control_run_id;

    SELECT rr.experiment_id, rr.as_of INTO variant_run_experiment_id, variant_as_of
    FROM research_runs rr WHERE rr.research_run_id=NEW.variant_run_id;

    IF control_experiment_id IS DISTINCT FROM expected_control_experiment_id THEN
        RAISE EXCEPTION 'RESEARCH_COMPARISON_CONTROL_RUN_MISMATCH' USING ERRCODE='23514';
    END IF;
    IF variant_run_experiment_id IS DISTINCT FROM NEW.variant_experiment_id THEN
        RAISE EXCEPTION 'RESEARCH_COMPARISON_VARIANT_RUN_MISMATCH' USING ERRCODE='23514';
    END IF;
    IF control_as_of IS DISTINCT FROM variant_as_of THEN
        RAISE EXCEPTION 'RESEARCH_COMPARISON_AS_OF_MISMATCH' USING ERRCODE='23514';
    END IF;

    SELECT result_fingerprint INTO stored_control_result_fingerprint
    FROM research_run_evidence WHERE research_run_id=NEW.control_run_id;
    SELECT result_fingerprint INTO stored_variant_result_fingerprint
    FROM research_run_evidence WHERE research_run_id=NEW.variant_run_id;

    IF stored_control_result_fingerprint IS NULL THEN
        RAISE EXCEPTION 'RESEARCH_COMPARISON_CONTROL_EVIDENCE_REQUIRED' USING ERRCODE='23514';
    END IF;
    IF stored_variant_result_fingerprint IS NULL THEN
        RAISE EXCEPTION 'RESEARCH_COMPARISON_VARIANT_EVIDENCE_REQUIRED' USING ERRCODE='23514';
    END IF;
    IF NEW.control_result_fingerprint IS DISTINCT FROM stored_control_result_fingerprint THEN
        RAISE EXCEPTION 'RESEARCH_COMPARISON_CONTROL_FINGERPRINT_MISMATCH' USING ERRCODE='23514';
    END IF;
    IF NEW.variant_result_fingerprint IS DISTINCT FROM stored_variant_result_fingerprint THEN
        RAISE EXCEPTION 'RESEARCH_COMPARISON_VARIANT_FINGERPRINT_MISMATCH' USING ERRCODE='23514';
    END IF;

    expected_comparison := jsonb_build_object(
        'schema', 'hope.research-comparison.v2',
        'variant_experiment_id', NEW.variant_experiment_id,
        'control', jsonb_build_object(
            'run_id', NEW.control_run_id::text,
            'result_fingerprint', stored_control_result_fingerprint
        ),
        'variant', jsonb_build_object(
            'run_id', NEW.variant_run_id::text,
            'result_fingerprint', stored_variant_result_fingerprint
        ),
        'evaluation_protocol_hash', expected_protocol_hash,
        'evaluation_results', expected_evaluation_results
    );

    IF NEW.canonical_comparison IS DISTINCT FROM expected_comparison THEN
        RAISE EXCEPTION 'RESEARCH_COMPARISON_CANONICAL_EVIDENCE_MISMATCH'
            USING ERRCODE='23514';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
