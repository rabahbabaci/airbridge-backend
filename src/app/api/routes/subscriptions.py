"""Stripe subscription management endpoints."""

import logging

import stripe
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from typing import Literal

from pydantic import BaseModel, Field

from app.api.middleware.auth import get_required_user
from app.core.config import settings
from app.db import get_db
from app.services.trial import get_tier_info

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


class CheckoutRequest(BaseModel):
    price_type: Literal["monthly", "annual"]
    success_url: str = Field(..., max_length=2000)
    cancel_url: str = Field(..., max_length=2000)


@router.post("/checkout")
async def create_checkout(
    body: CheckoutRequest,
    user=Depends(get_required_user),
    db=Depends(get_db),
):
    """Create a Stripe Checkout Session for subscription purchase."""
    if not settings.stripe_secret_key:
        return JSONResponse(status_code=503, content={"code": "STRIPE_NOT_CONFIGURED", "message": "Stripe is not configured"})

    stripe.api_key = settings.stripe_secret_key

    price_id = (
        settings.stripe_price_monthly
        if body.price_type == "monthly"
        else settings.stripe_price_annual
    )
    if not price_id:
        return JSONResponse(status_code=400, content={"code": "INVALID_PRICE", "message": f"Price type '{body.price_type}' is not configured"})

    # Create or reuse Stripe customer
    customer_id = user.stripe_customer_id
    if not customer_id:
        customer = stripe.Customer.create(
            email=user.email,
            metadata={"user_id": str(user.id)},
        )
        customer_id = customer.id
        if db is not None:
            user.stripe_customer_id = customer_id
            await db.commit()

    session = stripe.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=body.success_url,
        cancel_url=body.cancel_url,
    )

    return {"checkout_url": session.url}


@router.post("/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events. No auth — verified via Stripe signature."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    if not settings.stripe_webhook_secret:
        return JSONResponse(status_code=503, content={"code": "WEBHOOK_NOT_CONFIGURED", "message": "Webhook secret not configured"})

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.stripe_webhook_secret
        )
    except ValueError:
        return JSONResponse(status_code=400, content={"code": "INVALID_PAYLOAD", "message": "Invalid payload"})
    except stripe.error.SignatureVerificationError:
        return JSONResponse(status_code=400, content={"code": "INVALID_SIGNATURE", "message": "Invalid signature"})

    import app.db as _db
    from app.db.models import User
    from sqlalchemy import select

    if _db.async_session_factory is None:
        logger.warning("No DB configured, skipping webhook processing")
        return {"status": "ok"}

    event_type = event["type"]
    data = event["data"]["object"]

    async with _db.async_session_factory() as session:
        if event_type == "checkout.session.completed":
            customer_id = getattr(data, "customer", None)
            if customer_id:
                stmt = select(User).where(User.stripe_customer_id == customer_id)
                user = (await session.execute(stmt)).scalar_one_or_none()
                if user:
                    user.subscription_status = "active"
                    await session.commit()
                    logger.info("User %s subscription activated via checkout", user.id)

        elif event_type == "customer.subscription.deleted":
            customer_id = getattr(data, "customer", None)
            if customer_id:
                stmt = select(User).where(User.stripe_customer_id == customer_id)
                user = (await session.execute(stmt)).scalar_one_or_none()
                if user:
                    user.subscription_status = "cancelled"
                    await session.commit()
                    logger.info("User %s subscription cancelled", user.id)

        elif event_type == "invoice.payment_failed":
            customer_id = getattr(data, "customer", None)
            if customer_id:
                stmt = select(User).where(User.stripe_customer_id == customer_id)
                user = (await session.execute(stmt)).scalar_one_or_none()
                if user:
                    user.subscription_status = "past_due"
                    await session.commit()
                    logger.info("User %s subscription past_due", user.id)

    return {"status": "ok"}


@router.get("/status")
async def get_subscription_status(user=Depends(get_required_user)):
    """Return the current user's subscription status."""
    tier, remaining = get_tier_info(user)

    current_period_end = None
    if user.stripe_customer_id and settings.stripe_secret_key:
        try:
            stripe.api_key = settings.stripe_secret_key
            subscriptions = stripe.Subscription.list(
                customer=user.stripe_customer_id,
                status="active",
                limit=1,
            )
            if subscriptions.data:
                current_period_end = subscriptions.data[0].current_period_end
        except Exception:
            logger.exception("Failed to fetch Stripe subscription for user %s", user.id)

    return {
        "subscription_status": user.subscription_status,
        "stripe_customer_id": user.stripe_customer_id,
        "tier": tier,
        "trial_trips_remaining": remaining,
        "current_period_end": current_period_end,
    }


_NO_STRIPE_CUSTOMER_BODY = {
    "detail": "no_stripe_customer",
    "message": "No active Stripe subscription found for this account.",
}


@router.post("/portal")
async def create_portal_session(user=Depends(get_required_user)):
    """Create a Stripe Customer Portal session for managing subscription.

    Error differentiation (see frontend Settings for matching UX copy):
      404 no_stripe_customer — user.stripe_customer_id is null, or Stripe
                               says the customer no longer exists
      504 stripe_timeout     — Stripe API connection timed out
      503 stripe_error       — any other Stripe failure
    """
    if not settings.stripe_secret_key:
        return JSONResponse(status_code=503, content={"code": "STRIPE_NOT_CONFIGURED", "message": "Stripe is not configured"})

    if not user.stripe_customer_id:
        return JSONResponse(status_code=404, content=_NO_STRIPE_CUSTOMER_BODY)

    stripe.api_key = settings.stripe_secret_key

    try:
        session = stripe.billing_portal.Session.create(
            customer=user.stripe_customer_id,
        )
    except stripe.error.InvalidRequestError as e:
        # Stripe returns "No such customer: 'cus_xxx'" (code=resource_missing)
        # when the customer was deleted in Stripe but the DB still has a
        # stale ID. Surface as the same 404 shape as the null-ID case so
        # the frontend can show one consistent message.
        if getattr(e, "code", None) == "resource_missing" or "no such customer" in str(e).lower():
            logger.info("Stripe customer %s missing for user %s; returning 404", user.stripe_customer_id, user.id)
            return JSONResponse(status_code=404, content=_NO_STRIPE_CUSTOMER_BODY)
        logger.exception("Stripe InvalidRequestError opening portal for user %s", user.id)
        return JSONResponse(status_code=503, content={"code": "STRIPE_ERROR", "message": "Couldn't open the billing portal. Please try again."})
    except stripe.error.APIConnectionError:
        logger.warning("Stripe API connection timeout opening portal for user %s", user.id)
        return JSONResponse(status_code=504, content={"code": "STRIPE_TIMEOUT", "message": "Stripe is taking too long. Please try again."})
    except stripe.error.StripeError:
        logger.exception("Stripe error opening portal for user %s", user.id)
        return JSONResponse(status_code=503, content={"code": "STRIPE_ERROR", "message": "Couldn't open the billing portal. Please try again."})

    return {"portal_url": session.url}
