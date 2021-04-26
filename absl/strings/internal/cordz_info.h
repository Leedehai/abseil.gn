// Copyright 2019 The Abseil Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//      https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#ifndef ABSL_STRINGS_CORDZ_INFO_H_
#define ABSL_STRINGS_CORDZ_INFO_H_

#include <atomic>
#include <cstdint>
#include <functional>

#include "absl/base/config.h"
#include "absl/base/thread_annotations.h"
#include "absl/strings/internal/cord_internal.h"
#include "absl/strings/internal/cordz_functions.h"
#include "absl/strings/internal/cordz_handle.h"
#include "absl/strings/internal/cordz_statistics.h"
#include "absl/strings/internal/cordz_update_tracker.h"
#include "absl/synchronization/mutex.h"
#include "absl/types/span.h"

namespace absl {
ABSL_NAMESPACE_BEGIN
namespace cord_internal {

// CordzInfo tracks a profiled Cord. Each of these objects can be in two places.
// If a Cord is alive, the CordzInfo will be in the global_cordz_infos map, and
// can also be retrieved via the linked list starting with
// global_cordz_infos_head and continued via the cordz_info_next() method. When
// a Cord has reached the end of its lifespan, the CordzInfo object will be
// migrated out of the global_cordz_infos list and the global_cordz_infos_map,
// and will either be deleted or appended to the global_delete_queue. If it is
// placed on the global_delete_queue, the CordzInfo object will be cleaned in
// the destructor of a CordzSampleToken object.
class ABSL_LOCKABLE CordzInfo : public CordzHandle {
 public:
  using MethodIdentifier = CordzUpdateTracker::MethodIdentifier;

  // TrackCord creates a CordzInfo instance which tracks important metrics of
  // a sampled cord, and stores the created CordzInfo instance into `cord'. All
  // CordzInfo instances are placed in a global list which is used to discover
  // and snapshot all actively tracked cords. Callers are responsible for
  // calling UntrackCord() before the tracked Cord instance is deleted, or to
  // stop tracking the sampled Cord. Callers are also responsible for guarding
  // changes to the 'tree' value of a Cord (InlineData.tree) through the Lock()
  // and Unlock() calls. Any change resulting in a new tree value for the cord
  // requires a call to SetCordRep() before the old tree has been unreffed
  // and/or deleted. `method` identifies the Cord public API method initiating
  // the cord to be sampled.
  // Requires `cord` to hold a tree, and `cord.cordz_info()` to be null.
  static void TrackCord(InlineData& cord, MethodIdentifier method);

  // Identical to TrackCord(), except that this function fills the
  // `parent_stack` and `parent_method` properties of the returned CordzInfo
  // instance from the provided `src` instance if `src` is sampled.
  // This function should be used for sampling 'copy constructed' cords.
  static void TrackCord(InlineData& cord, const InlineData& src,
                        MethodIdentifier method);

  // Maybe sample the cord identified by 'cord' for method 'method'.
  // Uses `cordz_should_profile` to randomly pick cords to be sampled, and if
  // so, invokes `TrackCord` to start sampling `cord`.
  static void MaybeTrackCord(InlineData& cord, MethodIdentifier method);
  static void MaybeTrackCord(InlineData& cord, const InlineData& src,
                             MethodIdentifier method);

  // Stops tracking changes for a sampled cord, and deletes the provided info.
  // This function must be called before the sampled cord instance is deleted,
  // and before the root cordrep of the sampled cord is unreffed.
  // This function may extend the lifetime of the cordrep in cases where the
  // CordInfo instance is being held by a concurrent collection thread.
  static void UntrackCord(CordzInfo* cordz_info);

  CordzInfo() = delete;
  CordzInfo(const CordzInfo&) = delete;
  CordzInfo& operator=(const CordzInfo&) = delete;

  // Retrieves the oldest existing CordzInfo.
  static CordzInfo* Head(const CordzSnapshot& snapshot);

  // Retrieves the next oldest existing CordzInfo older than 'this' instance.
  CordzInfo* Next(const CordzSnapshot& snapshot) const;

  // Locks this instance for the update identified by `method`.
  // Increases the count for `method` in `update_tracker`.
  void Lock(MethodIdentifier method) ABSL_EXCLUSIVE_LOCK_FUNCTION(mutex_);

  // Unlocks this instance. If the contained `rep` has been set to null
  // indicating the Cord has been cleared or is otherwise no longer sampled,
  // then this method will delete this CordzInfo instance.
  void Unlock() ABSL_UNLOCK_FUNCTION(mutex_);

  // Asserts that this CordzInfo instance is locked.
  void AssertHeld() ABSL_ASSERT_EXCLUSIVE_LOCK(mutex_);

  // Updates the `rep' property of this instance. This methods is invoked by
  // Cord logic each time the root node of a sampled Cord changes, and before
  // the old root reference count is deleted. This guarantees that collection
  // code can always safely take a reference on the tracked cord.
  // Requires a lock to be held through the `Lock()` method.
  // TODO(b/117940323): annotate with ABSL_EXCLUSIVE_LOCKS_REQUIRED once all
  // Cord code is in a state where this can be proven true by the compiler.
  void SetCordRep(CordRep* rep);

  // Returns the current value of `rep_` for testing purposes only.
  CordRep* GetCordRepForTesting() const ABSL_NO_THREAD_SAFETY_ANALYSIS {
    return rep_;
  }

  // Returns the stack trace for where the cord was first sampled. Cords are
  // potentially sampled when they promote from an inlined cord to a tree or
  // ring representation, which is not necessarily the location where the cord
  // was first created. Some cords are created as inlined cords, and only as
  // data is added do they become a non-inlined cord. However, typically the
  // location represents reasonably well where the cord is 'created'.
  absl::Span<void* const> GetStack() const;

  // Returns the stack trace for a sampled cord's 'parent stack trace'. This
  // value may be set if the cord is sampled (promoted) after being created
  // from, or being assigned the value of an existing (sampled) cord.
  absl::Span<void* const> GetParentStack() const;

  // Retrieves the CordzStatistics associated with this Cord. The statistics
  // are only updated when a Cord goes through a mutation, such as an Append
  // or RemovePrefix.
  CordzStatistics GetCordzStatistics() const;

  // Records size metric for this CordzInfo instance.
  void RecordMetrics(int64_t size) {
    size_.store(size, std::memory_order_relaxed);
  }

 private:
  static constexpr int kMaxStackDepth = 64;

  explicit CordzInfo(CordRep* rep, const CordzInfo* src,
                     MethodIdentifier method);
  ~CordzInfo() override;

  void Track();
  void Untrack();

  // Returns the parent method from `src`, which is either `parent_method_` or
  // `method_` depending on `parent_method_` being kUnknown.
  // Returns kUnknown if `src` is null.
  static MethodIdentifier GetParentMethod(const CordzInfo* src);

  // Fills the provided stack from `src`, copying either `parent_stack_` or
  // `stack_` depending on `parent_stack_` being empty, returning the size of
  // the parent stack.
  // Returns 0 if `src` is null.
  static int FillParentStack(const CordzInfo* src, void** stack);

  // 'Unsafe' head/next/prev accessors not requiring the lock being held.
  // These are used exclusively for iterations (Head / Next) where we enforce
  // a token being held, so reading an 'old' / deleted pointer is fine.
  static CordzInfo* ci_head_unsafe() ABSL_NO_THREAD_SAFETY_ANALYSIS {
    return ci_head_.load(std::memory_order_acquire);
  }
  CordzInfo* ci_next_unsafe() const ABSL_NO_THREAD_SAFETY_ANALYSIS {
    return ci_next_.load(std::memory_order_acquire);
  }
  CordzInfo* ci_prev_unsafe() const ABSL_NO_THREAD_SAFETY_ANALYSIS {
    return ci_prev_.load(std::memory_order_acquire);
  }

  static absl::Mutex ci_mutex_;
  static std::atomic<CordzInfo*> ci_head_ ABSL_GUARDED_BY(ci_mutex_);
  std::atomic<CordzInfo*> ci_prev_ ABSL_GUARDED_BY(ci_mutex_){nullptr};
  std::atomic<CordzInfo*> ci_next_ ABSL_GUARDED_BY(ci_mutex_){nullptr};

  mutable absl::Mutex mutex_;
  CordRep* rep_ ABSL_GUARDED_BY(mutex_);

  void* stack_[kMaxStackDepth];
  void* parent_stack_[kMaxStackDepth];
  const int stack_depth_;
  const int parent_stack_depth_;
  const MethodIdentifier method_;
  const MethodIdentifier parent_method_;
  CordzUpdateTracker update_tracker_;
  const absl::Time create_time_;

  // Last recorded size for the cord.
  std::atomic<int64_t> size_{0};
};

inline ABSL_ATTRIBUTE_ALWAYS_INLINE void CordzInfo::MaybeTrackCord(
    InlineData& cord, MethodIdentifier method) {
  if (ABSL_PREDICT_FALSE(cordz_should_profile())) {
    TrackCord(cord, method);
  }
}

inline ABSL_ATTRIBUTE_ALWAYS_INLINE void CordzInfo::MaybeTrackCord(
    InlineData& cord, const InlineData& src, MethodIdentifier method) {
  if (ABSL_PREDICT_FALSE(cordz_should_profile())) {
    TrackCord(cord, src, method);
  }
}

inline void CordzInfo::AssertHeld() ABSL_ASSERT_EXCLUSIVE_LOCK(mutex_) {
#ifndef NDEBUG
  mutex_.AssertHeld();
#endif
}

inline void CordzInfo::SetCordRep(CordRep* rep) {
  AssertHeld();
  rep_ = rep;
  if (rep) {
    size_.store(rep->length);
  }
}

}  // namespace cord_internal
ABSL_NAMESPACE_END
}  // namespace absl

#endif  // ABSL_STRINGS_CORDZ_INFO_H_
