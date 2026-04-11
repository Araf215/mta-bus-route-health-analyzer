# Keep a number inside a fixed range
def clamp(value, low, high): # Helper function 
    return max(low, min(high, value)) # used to make sure values stay between 0-10


# Score based on how many detected buses are actually in service
def score_in_service_ratio(in_service_ratio):
    if in_service_ratio is None: # Don't score a non-existing metric
        return None
    return clamp(in_service_ratio * 10, 0, 10) # return ((total buses/in-service buses) * 10))


# Score based on how many in-service buses are actually moving
def score_movement_ratio(movement_ratio):
    if movement_ratio is None:  # Don't score a non-existing metric
        return None
    return clamp(movement_ratio * 10, 0, 10) # return (in-service buses/moving buses * 10)


# Layover is normal, however too many layover/no-progress buses may be an issue
def score_layover_ratio(layover_ratio):
    if layover_ratio is None: # Don't score a non-existing metric
        return None
    return clamp(10 - (layover_ratio * 10), 0, 10) # the number of layover buses and score are inversely related.


# Main route health scorer
def compute_route_health(total_vehicles, in_service_vehicles, movement_ratio=None, layover_ratio=None):
    total_buses = total_vehicles or 0 # total detected buses
    in_service_buses = in_service_vehicles or 0 # number of in-service buses

    base_weights = { # How each ratio is valued different towards the overall health score
        "in_service_ratio": 0.60, 
        "movement_ratio": 0.30,
        "layover_ratio": 0.10,
    }

    in_service_ratio = None # Default to missing unless it can be computed
    if total_buses > 0: # Prevents divison by zero = NaN
        in_service_ratio = in_service_buses / total_buses # In-service ratio formula

    in_service_score = score_in_service_ratio(in_service_ratio) # Ratio into score 
    movement_score = score_movement_ratio(movement_ratio) # Ratio into score 
    layover_score = score_layover_ratio(layover_ratio) # Ratio into score 

    scored_parts = [] # Used to only keep metrics with valid scores

    if in_service_score is not None:
        scored_parts.append(("in_service_ratio", in_service_score)) # Add in-service score as a tuple if it exists

    if movement_score is not None:
        scored_parts.append(("movement_ratio", movement_score)) # Add movement score as a tuple if it exists

    if layover_score is not None:
        scored_parts.append(("layover_ratio", layover_score)) # Add layover score as a tuple if it exists

    if scored_parts: # If there are valid scored parts
        total_weight = sum(base_weights[name] for name, _ in scored_parts) # Adds up the base weights of only the metrics that are present

        effective_weights = { # Recompute weights for existing metrics so that the sum is still 1.0
            name: round(base_weights[name] / total_weight, 3) # Round to 3 decimal places so the output is cleaner
            for name, _ in scored_parts
        }

        overall = round( # Weighted average score
            clamp(
                sum(score * effective_weights[name] for name, score in scored_parts), # multiplies each score by its effective weight and adds them together
                0,
                10,
            ), # Ensure the result of the weighted score stays in the 0-10 range
            2, # Round to two decimal places
        )
    else:
        effective_weights = {} # If every metric is missing return no valid score
        overall = None

    if overall is None: # No metrics exist
        label = "Unknown"
    elif overall >= 8: # If the average weighted score is 8+ (strong)
        label = "Strong"
    elif overall >= 6: # If the average weighted score is 6 - 7 (fair)
        label = "Fair"
    elif overall >= 4: # If the average weighted score is 4 - 5 (weak)
        label = "Weak"
    else: # If the average weighted score is under 4 (poor)
        label = "Poor"

    return { # Return ratios, scores, weights, health for the front end 
        "in_service_ratio": round(in_service_ratio, 3) if in_service_ratio is not None else None,
        "movement_ratio": round(movement_ratio, 3) if movement_ratio is not None else None,
        "layover_ratio": round(layover_ratio, 3) if layover_ratio is not None else None,
        "in_service_score": round(in_service_score, 2) if in_service_score is not None else None,
        "movement_score": round(movement_score, 2) if movement_score is not None else None,
        "layover_score": round(layover_score, 2) if layover_score is not None else None,
        "overall_score": overall,
        "health_label": label,
        "weights": effective_weights,
        "base_weights": base_weights,
    }